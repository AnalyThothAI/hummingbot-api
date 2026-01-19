"""
LP Position 管理器：负责 CLMM 仓位的开/关、查询与对账。
"""

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Dict, List, Optional, Set

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.gateway.gateway_lp import CLMMPoolInfo, CLMMPositionInfo

LogFunc = Callable[[int, str], None]


@dataclass
class ActionResult:
    """
    动作提交结果描述。

    Attributes:
        submitted: 是否成功提交动作。
        order_id: 相关订单 ID（若有）。
        error: 失败原因（若有）。
    """

    submitted: bool
    order_id: Optional[str] = None
    error: str = ""


class PendingOperation(Enum):
    """
    待处理操作类型（仅开仓 / 关仓）。
    """

    OPENING = "opening"
    CLOSING = "closing"


@dataclass
class PendingAction:
    """
    统一管理开仓/关仓的待处理状态。

    Attributes:
        operation: 操作类型（OPENING / CLOSING）。
        order_id: 订单 ID。
        started_ts: 操作开始时间戳。
        target_position_id: 目标仓位地址（关仓时使用）。
        pre_open_position_ids: 开仓前已有的仓位集合（用于发现新仓位）。
        budget_lock_id: 预算锁 ID（开仓锁定时使用）。
    """

    operation: PendingOperation
    order_id: str
    started_ts: float
    target_position_id: Optional[str] = None
    pre_open_position_ids: Set[str] = field(default_factory=set)
    budget_lock_id: Optional[str] = None

    def elapsed(self, now: float) -> float:
        """
        计算 pending 持续时间。

        Args:
            now: 当前时间戳。

        Returns:
            已持续秒数。
        """
        return now - self.started_ts if self.started_ts else 0.0


class PendingStatus(Enum):
    """
    Pending 状态处理结果枚举。
    """

    NONE = "none"
    OPEN_CONFIRMED = "open_confirmed"
    CLOSE_CONFIRMED = "close_confirmed"
    OPEN_TIMEOUT = "open_timeout"
    CLOSE_TIMEOUT = "close_timeout"
    FETCH_FAILED_TIMEOUT = "fetch_failed_timeout"


class PortfolioStatus(Enum):
    """
    仓位组合汇总状态。
    """

    OK = "ok"
    POOL_MISSING = "pool_missing"
    FETCH_FAILED = "fetch_failed"
    PRICE_UNAVAILABLE = "price_unavailable"


@dataclass
class PortfolioSummary:
    """
    仓位组合汇总结果（用于预算与风控）。

    Attributes:
        status: 汇总状态。
        active_count: 活跃仓位数量。
        active_ids: 活跃仓位地址列表。
        deployed_base: 已部署 base 数量。
        deployed_quote: 已部署 quote 数量。
        deployed_value_quote: 已部署价值（报价币计价）。
        price_used: 汇总估值使用的价格（可为空）。
    """

    status: PortfolioStatus
    active_count: int
    active_ids: List[str]
    deployed_base: Decimal
    deployed_quote: Decimal
    deployed_value_quote: Decimal
    price_used: Optional[Decimal]


@dataclass
class PendingResult:
    """
    Pending 处理结果。

    Attributes:
        status: 处理结果类型。
        pending: 当前 pending 状态快照。
        position: 确认后的仓位（开仓确认时使用）。
        detail: 额外标记信息（来源/原因）。
    """

    status: PendingStatus
    pending: Optional[PendingAction] = None
    position: Optional[CLMMPositionInfo] = None
    detail: str = ""


@dataclass
class PositionUpdate:
    """
    仓位刷新结果。

    Attributes:
        position: 更新后的仓位信息。
        cleared: 是否清理了本地仓位状态。
        clear_reason: 清理原因标签。
        close_value_quote: 估算的平仓价值。
        position_snapshot: 清理前的仓位快照（用于预算账本或审计）。
    """

    position: Optional[CLMMPositionInfo]
    cleared: bool = False
    clear_reason: str = ""
    close_value_quote: Optional[Decimal] = None
    position_snapshot: Optional[CLMMPositionInfo] = None


class LPPositionManager:
    """
    CLMM 仓位管理器，封装开/关仓、查询与对账。
    """

    def __init__(
        self,
        connectors: Dict[str, ConnectorBase],
        exchange: str,
        trading_pair: str,
        base_token: str,
        quote_token: str,
        pool_address: str = "",
        log_func: Optional[LogFunc] = None,
    ) -> None:
        """
        初始化 LPPositionManager。

        Args:
            connectors: 连接器映射。
            exchange: CLMM 连接器名称。
            trading_pair: 交易对。
            base_token: base 币种。
            quote_token: quote 币种。
            pool_address: 池地址（可选）。
            log_func: 日志函数（可选）。
        """
        self._connectors = connectors
        self._exchange = exchange
        self._trading_pair = trading_pair
        self._base_token = base_token
        self._quote_token = quote_token
        self._pool_address_override = pool_address
        self._log = log_func or self._default_log

        self.pool_info: Optional[CLMMPoolInfo] = None
        self.position_info: Optional[CLMMPositionInfo] = None
        self.current_position_id: Optional[str] = None
        self.pending_action: Optional[PendingAction] = None
        self._close_dust_threshold: Decimal = Decimal("0")

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数，使用模块 logger 输出。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    def set_close_dust_threshold(self, value: Decimal) -> None:
        """
        设置关仓视为“空仓”的报价币价值阈值。

        Args:
            value: 阈值。
        """
        self._close_dust_threshold = value

    async def get_pool_address(self) -> Optional[str]:
        """
        获取池地址（优先使用配置）。

        Returns:
            池地址。
        """
        if self._pool_address_override:
            return self._pool_address_override
        return await self._connectors[self._exchange].get_pool_address(self._trading_pair)

    async def fetch_positions_raw(self, pool_address: Optional[str]) -> Optional[List[CLMMPositionInfo]]:
        """
        拉取用户仓位原始列表。

        Args:
            pool_address: 池地址（可为 None）。

        Returns:
            仓位列表；失败时返回 None。
        """
        try:
            return await self._connectors[self._exchange].get_user_positions(pool_address=pool_address)
        except Exception as e:
            self._log(logging.DEBUG, f"fetch_positions_raw error: {e}")
            return None

    def filter_positions_for_pair(
        self, positions: List[CLMMPositionInfo], pool_address: Optional[str]
    ) -> Optional[List[CLMMPositionInfo]]:
        """
        按池地址过滤仓位，避免误选到其他池子仓位。

        Args:
            positions: 原始仓位列表。
            pool_address: 池地址（可为 None）。

        Returns:
            过滤后的仓位列表；池地址无效时返回 None。
        """
        if not positions:
            return []
        if not pool_address:
            self._log(logging.WARNING, "filter_positions_for_pair: pool_address missing.")
            return None
        return [p for p in positions if getattr(p, "pool_address", None) == pool_address]

    async def fetch_positions_in_pool(self, pool_address: Optional[str]) -> Optional[List[CLMMPositionInfo]]:
        """
        拉取并按池地址过滤用户仓位。

        Args:
            pool_address: 池地址（可为 None）。

        Returns:
            过滤后的仓位列表；失败时返回 None。
        """
        if not pool_address:
            self._log(logging.WARNING, "fetch_positions_in_pool: pool_address missing.")
            return None
        positions = await self.fetch_positions_raw(pool_address)
        if positions is None:
            return None
        return self.filter_positions_for_pair(positions, pool_address)

    async def summarize_positions(
        self, pool_address: Optional[str], price: Optional[Decimal] = None
    ) -> PortfolioSummary:
        """
        汇总链上仓位信息，用于预算与风险控制。

        Args:
            pool_address: 池地址。
            price: 估值价格（可选，未提供时尝试从仓位/池信息读取）。

        Returns:
            组合汇总结果。
        """
        if not pool_address:
            return PortfolioSummary(
                status=PortfolioStatus.POOL_MISSING,
                active_count=0,
                active_ids=[],
                deployed_base=Decimal("0"),
                deployed_quote=Decimal("0"),
                deployed_value_quote=Decimal("0"),
                price_used=price,
            )

        positions = await self.fetch_positions_in_pool(pool_address)
        if positions is None:
            return PortfolioSummary(
                status=PortfolioStatus.FETCH_FAILED,
                active_count=0,
                active_ids=[],
                deployed_base=Decimal("0"),
                deployed_quote=Decimal("0"),
                deployed_value_quote=Decimal("0"),
                price_used=price,
            )

        active_ids: List[str] = []
        deployed_base = Decimal("0")
        deployed_quote = Decimal("0")
        deployed_value = Decimal("0")
        missing_price = False

        for pos in positions:
            pos_price = price if price is not None else self._resolve_position_price(pos)
            if self._is_position_effectively_closed(pos, pos_price):
                continue

            active_ids.append(pos.address)
            totals = self._position_token_totals(pos)
            if totals is None:
                missing_price = True
                continue
            base_amt, quote_amt = totals
            deployed_base += base_amt
            deployed_quote += quote_amt
            if pos_price is None:
                missing_price = True
                continue
            deployed_value += base_amt * pos_price + quote_amt

        status = PortfolioStatus.OK
        if active_ids and missing_price:
            status = PortfolioStatus.PRICE_UNAVAILABLE

        return PortfolioSummary(
            status=status,
            active_count=len(active_ids),
            active_ids=active_ids,
            deployed_base=deployed_base,
            deployed_quote=deployed_quote,
            deployed_value_quote=deployed_value,
            price_used=price,
        )

    def select_active_position(self, positions: List[CLMMPositionInfo]) -> Optional[CLMMPositionInfo]:
        """
        从仓位列表中选择可复用的活动仓位。

        Args:
            positions: 用户仓位列表。

        Returns:
            活动仓位；若不存在则返回 None。
        """
        for pos in reversed(positions):
            price = self._resolve_position_price(pos)
            if not self._is_position_effectively_closed(pos, price):
                return pos
        return None

    async def attempt_adopt_existing_position(self, pool_address: Optional[str]) -> Optional[CLMMPositionInfo]:
        """
        尝试复用已有仓位（仅返回候选，不更新本地状态）。

        Args:
            pool_address: 池地址。

        Returns:
            活动仓位；若不存在则返回 None。
        """
        positions = await self.fetch_positions_in_pool(pool_address)
        if positions is None or not positions:
            return None
        return self.select_active_position(positions)

    def confirm_open(self, position: CLMMPositionInfo) -> None:
        """
        确认开仓，更新本地状态并清理 pending。

        Args:
            position: 仓位信息。
        """
        self.current_position_id = position.address
        self.position_info = position
        self.clear_pending()

    def complete_close(self) -> None:
        """
        确认关仓，清理本地仓位状态与 pending。
        """
        self.current_position_id = None
        self.position_info = None
        self.clear_pending()

    async def update_position_info(self) -> PositionUpdate:
        """
        更新当前仓位信息（空仓位会清理本地状态）。

        Returns:
            仓位刷新结果。
        """
        if not self.current_position_id:
            return PositionUpdate(position=None)

        pool_address = await self.get_pool_address()
        positions = await self.fetch_positions_raw(pool_address)
        if positions is None:
            return PositionUpdate(position=None)

        target_pos = next((p for p in positions if p.address == self.current_position_id), None)
        if target_pos is None:
            close_value = self._estimate_position_value(self.position_info)
            snapshot = self.position_info
            self.complete_close()
            return PositionUpdate(
                position=None,
                cleared=True,
                clear_reason="missing",
                close_value_quote=close_value,
                position_snapshot=snapshot,
            )

        price = self._resolve_position_price(target_pos)
        if self._is_position_effectively_closed(target_pos, price):
            close_value = self._estimate_position_value(target_pos)
            snapshot = target_pos
            self.complete_close()
            return PositionUpdate(
                position=None,
                cleared=True,
                clear_reason="empty",
                close_value_quote=close_value,
                position_snapshot=snapshot,
            )

        self.position_info = target_pos
        return PositionUpdate(position=target_pos)

    async def open_position(
        self,
        price: float,
        upper_width_pct: float,
        lower_width_pct: float,
        base_amount: float,
        quote_amount: float,
        budget_lock_id: Optional[str] = None,
    ) -> ActionResult:
        """
        提交开仓请求并设置 pending。

        Args:
            price: 当前价格。
            upper_width_pct: 上半宽度百分比。
            lower_width_pct: 下半宽度百分比。
            base_amount: base 数量。
            quote_amount: quote 数量。
            budget_lock_id: 预算锁 ID（可选）。

        Returns:
            动作提交结果。
        """
        try:
            pool_address = await self.get_pool_address()
            positions = await self.fetch_positions_in_pool(pool_address)
            pre_open_ids = {p.address for p in positions} if positions else set()

            connector = self._connectors[self._exchange]
            order_id = connector.add_liquidity(
                trading_pair=self._trading_pair,
                price=price,
                upper_width_pct=upper_width_pct,
                lower_width_pct=lower_width_pct,
                base_token_amount=base_amount,
                quote_token_amount=quote_amount,
            )
            self.set_pending_action(PendingAction(
                operation=PendingOperation.OPENING,
                order_id=str(order_id),
                started_ts=time.time(),
                pre_open_position_ids=pre_open_ids,
                budget_lock_id=budget_lock_id,
            ))
            return ActionResult(submitted=True, order_id=str(order_id))
        except Exception as e:
            self.clear_pending()
            return ActionResult(submitted=False, error=str(e))

    async def close_position(self) -> ActionResult:
        """
        提交关仓请求并设置 pending。

        Returns:
            动作提交结果。
        """
        if not self.current_position_id:
            return ActionResult(submitted=False, error="no_position")
        try:
            connector = self._connectors[self._exchange]
            order_id = connector.remove_liquidity(
                trading_pair=self._trading_pair,
                position_address=self.current_position_id,
            )
            self.set_pending_action(PendingAction(
                operation=PendingOperation.CLOSING,
                order_id=str(order_id),
                started_ts=time.time(),
                target_position_id=self.current_position_id,
            ))
            return ActionResult(submitted=True, order_id=str(order_id))
        except Exception as e:
            self.clear_pending()
            return ActionResult(submitted=False, error=str(e))

    async def handle_pending_action(self, open_timeout_sec: int, close_timeout_sec: int) -> PendingResult:
        """
        处理 pending 状态，返回处理结果。

        Args:
            open_timeout_sec: 开仓超时秒数。
            close_timeout_sec: 关仓超时秒数。

        Returns:
            PendingResult。
        """
        if self.pending_action is None:
            return PendingResult(status=PendingStatus.NONE)

        pending = self.pending_action
        now = time.time()
        elapsed = pending.elapsed(now)
        pool_address = await self.get_pool_address()
        positions = await self.fetch_positions_in_pool(pool_address)

        if positions is None:
            if self._pending_timed_out(pending, elapsed, open_timeout_sec, close_timeout_sec):
                return PendingResult(status=PendingStatus.FETCH_FAILED_TIMEOUT, pending=pending)
            return PendingResult(status=PendingStatus.NONE, pending=pending)

        ids = {p.address for p in positions} if positions else set()

        if pending.operation == PendingOperation.OPENING:
            new_ids = ids - pending.pre_open_position_ids
            if new_ids:
                new_positions = [p for p in positions if p.address in new_ids]
                new_pos = self.select_active_position(new_positions)
                if new_pos:
                    return PendingResult(
                        status=PendingStatus.OPEN_CONFIRMED,
                        pending=pending,
                        position=new_pos,
                        detail="poll",
                    )

            if elapsed >= open_timeout_sec:
                adopted = await self.attempt_adopt_existing_position(pool_address)
                if adopted:
                    return PendingResult(
                        status=PendingStatus.OPEN_CONFIRMED,
                        pending=pending,
                        position=adopted,
                        detail="adopt",
                    )
                return PendingResult(status=PendingStatus.OPEN_TIMEOUT, pending=pending)
            return PendingResult(status=PendingStatus.NONE, pending=pending)

        if pending.operation == PendingOperation.CLOSING:
            target_id = pending.target_position_id
            if not target_id:
                return PendingResult(status=PendingStatus.CLOSE_TIMEOUT, pending=pending)

            target_pos = next((p for p in positions if p.address == target_id), None)
            if target_pos is None:
                return PendingResult(
                    status=PendingStatus.CLOSE_CONFIRMED,
                    pending=pending,
                    detail="missing",
                )

            price = self._resolve_position_price(target_pos)
            if self._is_position_effectively_closed(target_pos, price):
                return PendingResult(
                    status=PendingStatus.CLOSE_CONFIRMED,
                    pending=pending,
                    detail="empty",
                )

            if elapsed >= close_timeout_sec:
                return PendingResult(status=PendingStatus.CLOSE_TIMEOUT, pending=pending)

        return PendingResult(status=PendingStatus.NONE, pending=pending)

    async def reconcile_close_after_failure(self, target_id: Optional[str]) -> bool:
        """
        关仓失败后对账，判断仓位是否已关闭。

        Args:
            target_id: 目标仓位地址。

        Returns:
            是否确认已关闭。
        """
        if not target_id:
            return False
        pool_address = await self.get_pool_address()
        positions = await self.fetch_positions_in_pool(pool_address)
        if positions is None:
            return False
        target_pos = next((p for p in positions if p.address == target_id), None)
        if target_pos is None:
            return True
        price = self._resolve_position_price(target_pos)
        return self._is_position_effectively_closed(target_pos, price)

    async def collect_fees(self) -> bool:
        """
        提交收取手续费请求（通过 gateway 接口）。

        Returns:
            是否成功提交手续费收取请求。
        """
        if not self.current_position_id:
            return False
        try:
            connector = self._connectors[self._exchange]
            result = await connector._get_gateway_instance().clmm_collect_fees(
                connector=connector.connector_name,
                network=connector.network,
                wallet_address=connector.address,
                position_address=self.current_position_id,
            )
            tx_sig = result.get("signature")
            extra = f" tx={tx_sig}" if tx_sig else ""
            self._log(logging.INFO, f"Collect fees submitted for {self.current_position_id}{extra}")
            return True
        except Exception as e:
            self._log(logging.ERROR, f"collect_fees error: {e}")
            return False

    def set_pending_action(self, pending: PendingAction) -> None:
        """
        设置 pending 状态。

        Args:
            pending: pending 对象。
        """
        self.pending_action = pending

    def clear_pending(self) -> None:
        """
        清理 pending 状态。
        """
        self.pending_action = None

    def _pending_timed_out(
        self,
        pending: PendingAction,
        elapsed: float,
        open_timeout_sec: int,
        close_timeout_sec: int,
    ) -> bool:
        """
        判断 pending 是否超时。

        Args:
            pending: pending 状态对象。
            elapsed: 已等待秒数。
            open_timeout_sec: 开仓超时。
            close_timeout_sec: 关仓超时。

        Returns:
            是否超时。
        """
        if pending.operation == PendingOperation.OPENING:
            return elapsed >= open_timeout_sec
        if pending.operation == PendingOperation.CLOSING:
            return elapsed >= close_timeout_sec
        return False

    def _resolve_position_price(self, position: CLMMPositionInfo) -> Optional[Decimal]:
        """
        解析用于估值的价格，优先使用最新池子价格。

        Args:
            position: 仓位信息。

        Returns:
            估值价格；若不可用则返回 None。
        """
        try:
            if self.pool_info is not None:
                return Decimal(str(self.pool_info.price))
            price = getattr(position, "price", None)
            if price is None:
                return None
            return Decimal(str(price))
        except Exception:
            return None

    def _position_token_totals(self, position: CLMMPositionInfo) -> Optional[tuple[Decimal, Decimal]]:
        """
        解析仓位的 token 数量。

        Args:
            position: 仓位信息。

        Returns:
            base 与 quote 数量。
        """
        try:
            base = Decimal(str(getattr(position, "base_token_amount", "0")))
            quote = Decimal(str(getattr(position, "quote_token_amount", "0")))
            return base, quote
        except Exception:
            return None

    def position_token_totals(
        self, position: CLMMPositionInfo, include_fees: bool = False
    ) -> Optional[tuple[Decimal, Decimal]]:
        """
        获取仓位的 base/quote 总量。

        Args:
            position: 仓位信息。
            include_fees: 是否包含未收取手续费。

        Returns:
            base 与 quote 数量；解析失败返回 None。
        """
        totals = self._position_token_totals(position)
        if totals is None:
            return None
        base_amt, quote_amt = totals
        if not include_fees:
            return base_amt, quote_amt
        try:
            base_fee = Decimal(str(getattr(position, "base_fee_amount", "0")))
            quote_fee = Decimal(str(getattr(position, "quote_fee_amount", "0")))
            return base_amt + base_fee, quote_amt + quote_fee
        except Exception:
            return base_amt, quote_amt

    def position_fee_value_in_quote(self, position: CLMMPositionInfo, price: Decimal) -> Optional[Decimal]:
        """
        计算未收取手续费的报价币价值。

        Args:
            position: 仓位信息。
            price: 当前价格（报价币计价）。

        Returns:
            手续费价值（报价币计价）；解析失败时返回 None。
        """
        try:
            base_fee = Decimal(str(getattr(position, "base_fee_amount", "0")))
            quote_fee = Decimal(str(getattr(position, "quote_fee_amount", "0")))
            return base_fee * price + quote_fee
        except Exception:
            return None

    def position_has_pending_fees(self, position: CLMMPositionInfo) -> bool:
        """
        判断仓位是否存在待收取手续费。

        Args:
            position: 仓位信息。

        Returns:
            是否存在待收取手续费。
        """
        try:
            base_fee = Decimal(str(getattr(position, "base_fee_amount", "0")))
            quote_fee = Decimal(str(getattr(position, "quote_fee_amount", "0")))
            return base_fee > 0 or quote_fee > 0
        except Exception:
            return False

    def _position_total_value_in_quote(self, position: CLMMPositionInfo, price: Decimal) -> Optional[Decimal]:
        """
        估算仓位按报价币计价的价值。

        Args:
            position: 仓位信息。
            price: 当前价格。

        Returns:
            报价币价值。
        """
        totals = self._position_token_totals(position)
        if totals is None:
            return None
        base_total, quote_total = totals
        return base_total * price + quote_total

    def position_value_in_quote(
        self, position: CLMMPositionInfo, price: Decimal, include_fees: bool = False
    ) -> Optional[Decimal]:
        """
        估算仓位按报价币计价的价值。

        Args:
            position: 仓位信息。
            price: 当前价格。
            include_fees: 是否包含未收取手续费。

        Returns:
            报价币价值；无法估算时返回 None。
        """
        totals = self.position_token_totals(position, include_fees=include_fees)
        if totals is None:
            return None
        base_total, quote_total = totals
        return base_total * price + quote_total

    def _position_liquidity(self, position: CLMMPositionInfo) -> Optional[Decimal]:
        """
        解析仓位流动性数值。

        Args:
            position: 仓位信息。

        Returns:
            流动性值。
        """
        try:
            liquidity = getattr(position, "liquidity", None)
            if liquidity is None:
                return None
            return Decimal(str(liquidity))
        except Exception:
            return None

    def _is_position_effectively_closed(self, position: CLMMPositionInfo, price: Optional[Decimal]) -> bool:
        """
        判断仓位是否可视为已关（流动性为 0 或价值接近 0）。

        Args:
            position: 仓位信息。
            price: 当前价格（报价币计价），可为空。

        Returns:
            是否可视为已关。
        """
        totals = self._position_token_totals(position)
        if totals is not None:
            base_total, quote_total = totals
            if base_total <= 0 and quote_total <= 0:
                return True

        liquidity = self._position_liquidity(position)
        if liquidity is not None and liquidity <= 0:
            return True

        if price is None:
            return False

        total_value = self._position_total_value_in_quote(position, price)
        if total_value is None:
            return False
        return total_value <= self._close_dust_threshold

    def _estimate_position_value(self, position: Optional[CLMMPositionInfo]) -> Optional[Decimal]:
        """
        估算仓位按报价币计价的价值。

        Args:
            position: 仓位信息。

        Returns:
            报价币价值；不可估算时返回 None。
        """
        if position is None:
            return None
        price = self._resolve_position_price(position)
        if price is None:
            return None
        return self._position_total_value_in_quote(position, price)
