"""
Swap 管理器：负责路由换仓与余额确认。
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, Optional, Tuple

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.in_flight_order import OrderState

LogFunc = Callable[[int, str], None]
IsPausedFunc = Callable[[], bool]


@dataclass
class SwapState:
    """
    跟踪路由换仓状态。

    Attributes:
        in_progress: 是否在进行中。
        order_id: 路由订单 ID。
        started_ts: 开始时间戳。
        label: 标记信息（日志用）。
        direction: 方向（BUY/SELL）。
    """

    in_progress: bool = False
    order_id: Optional[str] = None
    started_ts: float = 0.0
    label: str = ""
    direction: str = ""


class RouterSwapManager:
    """
    路由换仓管理器（Jupiter/Router）。
    """

    def __init__(
        self,
        connectors: Dict[str, ConnectorBase],
        router: str,
        trading_pair: str,
        base_token: str,
        quote_token: str,
        log_func: Optional[LogFunc] = None,
        is_paused_func: Optional[IsPausedFunc] = None,
        swap_timeout_sec: int = 120,
        swap_poll_interval_sec: Decimal = Decimal("2"),
        swap_slippage_pct: Decimal = Decimal("1"),
        swap_retry_attempts: int = 0,
        swap_retry_delay_sec: float = 1.0,
    ) -> None:
        """
        初始化 RouterSwapManager。

        Args:
            connectors: 连接器映射。
            router: 路由连接器名称。
            trading_pair: 交易对。
            base_token: base 币种。
            quote_token: quote 币种。
            log_func: 日志函数（可选）。
            is_paused_func: 是否暂停交易的回调（可选）。
            swap_timeout_sec: 换仓超时秒数。
            swap_poll_interval_sec: 轮询间隔。
            swap_slippage_pct: 报价滑点百分比。
            swap_retry_attempts: 换仓重试次数（仅限下单前失败）。
            swap_retry_delay_sec: 重试间隔秒数。
        """
        self._connectors = connectors
        self._router = router
        self._trading_pair = trading_pair
        self._base_token = base_token
        self._quote_token = quote_token
        self._log = log_func or self._default_log
        self._is_paused = is_paused_func or (lambda: False)
        self._swap_timeout_sec = swap_timeout_sec
        self._swap_poll_interval_sec = swap_poll_interval_sec
        self._swap_slippage_pct = swap_slippage_pct
        self._swap_retry_attempts = max(0, int(swap_retry_attempts))
        self._swap_retry_delay_sec = max(0.0, float(swap_retry_delay_sec))

        self._wallet_base: Optional[Decimal] = None
        self._wallet_quote: Optional[Decimal] = None
        self.state: SwapState = SwapState()

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数，使用模块 logger 输出。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    @property
    def in_progress(self) -> bool:
        """
        是否有进行中的 swap。

        Returns:
            是否正在换仓。
        """
        return self.state.in_progress

    async def update_balances(self) -> None:
        """
        更新路由器余额缓存。

        Returns:
            None.
        """
        router = self._connectors[self._router]
        try:
            await router.update_balances(on_interval=False)
        except Exception:
            return
        try:
            self._wallet_base = Decimal(str(router.get_balance(self._base_token) or 0))
            self._wallet_quote = Decimal(str(router.get_balance(self._quote_token) or 0))
        except Exception:
            return

    async def get_balances(self) -> Tuple[Decimal, Decimal]:
        """
        获取钱包余额。

        Returns:
            base 与 quote 的余额。
        """
        await self.update_balances()
        base_bal = self._wallet_base if self._wallet_base is not None else Decimal("0")
        quote_bal = self._wallet_quote if self._wallet_quote is not None else Decimal("0")
        return base_bal, quote_bal

    async def swap(self, is_buy: bool, amount_base: Decimal, label: str, allow_when_paused: bool) -> bool:
        """
        通过路由执行换仓。

        Args:
            is_buy: True 表示买入 base（花费 quote）。
            amount_base: base 数量。
            label: 日志标记。
            allow_when_paused: 暂停时是否允许执行。

        Returns:
            是否成功完成换仓。
        """
        return await self._swap_internal(
            trading_pair=self._trading_pair,
            is_buy=is_buy,
            amount_in=amount_base,
            label=label,
            allow_when_paused=allow_when_paused,
        )

    async def swap_by_quote(self, quote_amount: Decimal, label: str, allow_when_paused: bool) -> bool:
        """
        通过路由以 quote 作为输入执行换仓（精确输入）。

        Args:
            quote_amount: quote 数量。
            label: 日志标记。
            allow_when_paused: 暂停时是否允许执行。

        Returns:
            是否成功完成换仓。
        """
        reverse_pair = f"{self._quote_token}-{self._base_token}"
        return await self._swap_internal(
            trading_pair=reverse_pair,
            is_buy=False,
            amount_in=quote_amount,
            label=label,
            allow_when_paused=allow_when_paused,
        )

    async def _swap_internal(
        self,
        trading_pair: str,
        is_buy: bool,
        amount_in: Decimal,
        label: str,
        allow_when_paused: bool,
    ) -> bool:
        """
        执行换仓的统一内部实现。

        Args:
            trading_pair: 交易对。
            is_buy: True 表示买入 base（花费 quote）。
            amount_in: 交易对 base 数量（swap_by_quote 会传入 quote 数量）。
            label: 日志标记。
            allow_when_paused: 暂停时是否允许执行。

        Returns:
            是否成功完成换仓。
        """
        if amount_in <= 0:
            return True
        if self._is_paused() and not allow_when_paused:
            return False
        for attempt in range(self._swap_retry_attempts + 1):
            success, retryable = await self._swap_attempt(
                trading_pair=trading_pair,
                is_buy=is_buy,
                amount_in=amount_in,
                label=label,
            )
            if success:
                return True
            if not retryable or attempt >= self._swap_retry_attempts:
                return False
            await asyncio.sleep(self._swap_retry_delay_sec)
        return False

    async def _swap_attempt(
        self,
        trading_pair: str,
        is_buy: bool,
        amount_in: Decimal,
        label: str,
    ) -> tuple[bool, bool]:
        """
        执行一次换仓尝试。

        Args:
            trading_pair: 交易对。
            is_buy: 是否买入 base。
            amount_in: 交易对 base 数量（swap_by_quote 会传入 quote 数量）。
            label: 日志标记。

        Returns:
            (success, retryable)。
        """
        router = self._connectors[self._router]
        await self.update_balances()
        pair_base, pair_quote = trading_pair.split("-")
        wallet_map = {
            self._base_token: self._wallet_base or Decimal("0"),
            self._quote_token: self._wallet_quote or Decimal("0"),
        }
        base_before = wallet_map.get(pair_base, Decimal("0"))
        quote_before = wallet_map.get(pair_quote, Decimal("0"))

        try:
            quote_price = await router.get_quote_price(
                trading_pair=trading_pair,
                is_buy=is_buy,
                amount=amount_in,
                slippage_pct=self._swap_slippage_pct,
            )
        except Exception as e:
            self._log(
                logging.ERROR,
                "Router get_quote_price failed: "
                f"{e} (pair={trading_pair} is_buy={is_buy} amount={amount_in} slippage={self._swap_slippage_pct})",
            )
            return False, True
        if quote_price is None or quote_price <= 0:
            self._log(
                logging.ERROR,
                "Router get_quote_price returned invalid price "
                f"(pair={trading_pair} is_buy={is_buy} amount={amount_in} "
                f"slippage={self._swap_slippage_pct} price={quote_price})",
            )
            return False, True

        try:
            order_id = router.place_order(
                is_buy=is_buy,
                trading_pair=trading_pair,
                amount=amount_in,
                price=quote_price,
            )
        except Exception as e:
            self._log(
                logging.ERROR,
                f"Router place_order failed: {e} (pair={trading_pair} is_buy={is_buy} amount={amount_in})",
            )
            return False, False

        self.state = SwapState(
            in_progress=True,
            order_id=str(order_id),
            started_ts=time.time(),
            label=label,
            direction="BUY" if is_buy else "SELL",
        )

        start = time.time()
        while time.time() - start < self._swap_timeout_sec:
            await asyncio.sleep(float(self._swap_poll_interval_sec))
            await self.update_balances()
            wallet_map = {
                self._base_token: self._wallet_base or Decimal("0"),
                self._quote_token: self._wallet_quote or Decimal("0"),
            }
            base_after = wallet_map.get(pair_base, Decimal("0"))
            quote_after = wallet_map.get(pair_quote, Decimal("0"))
            if is_buy and base_after > base_before:
                self.state = SwapState()
                return True, False
            if (not is_buy) and quote_after > quote_before:
                self.state = SwapState()
                return True, False

        await self.update_balances()
        wallet_map = {
            self._base_token: self._wallet_base or Decimal("0"),
            self._quote_token: self._wallet_quote or Decimal("0"),
        }
        base_after = wallet_map.get(pair_base, Decimal("0"))
        quote_after = wallet_map.get(pair_quote, Decimal("0"))
        if is_buy and base_after > base_before:
            self.state = SwapState()
            return True, False
        if (not is_buy) and quote_after > quote_before:
            self.state = SwapState()
            return True, False

        self.state = SwapState()
        state = await self._confirm_order_status(order_id, trading_pair)
        if state == OrderState.FILLED:
            return True, False
        if state in {OrderState.FAILED, OrderState.CANCELED}:
            return False, True
        self._log(logging.ERROR, f"Router swap timeout order_id={order_id} pair={trading_pair}")
        return False, False

    async def _confirm_order_status(
        self, order_id: str, trading_pair: str
    ) -> Optional[OrderState]:
        """
        确认订单状态，避免超时后重复下单。

        Args:
            order_id: 客户端订单 ID。
            trading_pair: 交易对。

        Returns:
            订单状态；不可确认时返回 None。
        """
        router = self._connectors[self._router]
        order = router.get_order(order_id)
        if order is None:
            self._log(
                logging.WARNING,
                f"Swap order not found for status confirm. id={order_id} pair={trading_pair}",
            )
            return None

        if order.is_done:
            return order.current_state

        try:
            await router.update_order_status([order])
        except Exception as e:
            self._log(
                logging.WARNING,
                f"Swap order status update failed: {e} (id={order_id} pair={trading_pair})",
            )
            return order.current_state
        return order.current_state
