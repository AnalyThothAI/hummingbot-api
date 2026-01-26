import asyncio
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

from .components import BalanceEvent, BalanceEventKind, LPView, PoolDomainAdapter, Snapshot, SwapPurpose, SwapView

if TYPE_CHECKING:
    from ..clmm_lp_base import CLMMLPBaseConfig
    from .components import OpenProposal


class SnapshotBuilder:
    def __init__(
        self,
        *,
        controller_id: str,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        market_data_provider,
        pool_price_provider: Optional[Callable[[], Optional[Decimal]]] = None,
    ) -> None:
        self._controller_id = controller_id
        self._config = config
        self._domain = domain
        self._market_data_provider = market_data_provider
        self._pool_price_provider = pool_price_provider

    def build(
        self,
        *,
        now: float,
        executors_info: List[ExecutorInfo],
        wallet_base: Decimal,
        wallet_quote: Decimal,
        snapshot_wallet_base: Optional[Decimal] = None,
        snapshot_wallet_quote: Optional[Decimal] = None,
    ) -> Snapshot:
        current_price = self._get_current_price()
        if snapshot_wallet_base is None:
            snapshot_wallet_base = wallet_base
        if snapshot_wallet_quote is None:
            snapshot_wallet_quote = wallet_quote

        lp: Dict[str, LPView] = {}
        swaps: Dict[str, SwapView] = {}
        balance_events: List[BalanceEvent] = []
        for executor in executors_info:
            if executor.controller_id != self._controller_id:
                continue
            if executor.type == "lp_position_executor":
                lp[executor.id] = self._parse_lp_view(executor)
                lp_event = self._parse_lp_balance_event(executor, now)
                if lp_event is not None:
                    balance_events.append(lp_event)
            elif executor.type == "gateway_swap_executor":
                level_id = getattr(executor.config, "level_id", None)
                swaps[executor.id] = SwapView(
                    executor_id=executor.id,
                    is_active=executor.is_active,
                    is_done=executor.is_done,
                    close_type=executor.close_type,
                    timestamp=float(executor.timestamp or 0),
                    level_id=level_id,
                    purpose=self._swap_purpose(level_id),
                    amount=Decimal(str(getattr(executor.config, "amount", 0))),
                )
                swap_event = self._parse_swap_balance_event(executor, now)
                if swap_event is not None:
                    balance_events.append(swap_event)

        active_lp = [v for v in lp.values() if v.is_active]
        active_swaps = [v for v in swaps.values() if v.is_active]
        return Snapshot(
            now=now,
            current_price=current_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            snapshot_wallet_base=snapshot_wallet_base,
            snapshot_wallet_quote=snapshot_wallet_quote,
            lp=lp,
            swaps=swaps,
            active_lp=active_lp,
            active_swaps=active_swaps,
            balance_events=balance_events,
        )

    def _parse_lp_view(self, executor: ExecutorInfo) -> LPView:
        custom = executor.custom_info or {}
        lp_base_amount = Decimal(str(custom.get("base_amount", 0)))
        lp_quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        lp_base_fee = Decimal(str(custom.get("base_fee", 0)))
        lp_quote_fee = Decimal(str(custom.get("quote_fee", 0)))

        inverted = self._domain.executor_token_order_inverted(executor)
        if inverted is None:
            inverted = self._domain.pool_order_inverted
        base_amount, quote_amount = self._domain.pool_amounts_to_strategy(lp_base_amount, lp_quote_amount, inverted)
        base_fee, quote_fee = self._domain.pool_amounts_to_strategy(lp_base_fee, lp_quote_fee, inverted)

        lower = custom.get("lower_price")
        upper = custom.get("upper_price")
        price = custom.get("current_price")
        lower = Decimal(str(lower)) if lower is not None else None
        upper = Decimal(str(upper)) if upper is not None else None
        price = Decimal(str(price)) if price is not None else None
        if lower is not None and upper is not None:
            lower, upper = self._domain.pool_bounds_to_strategy(lower, upper, inverted)
        if price is not None:
            price = self._domain.pool_price_to_strategy(price, inverted)
        out_of_range_since = custom.get("out_of_range_since")
        out_of_range_since = float(out_of_range_since) if out_of_range_since is not None else None

        return LPView(
            executor_id=executor.id,
            is_active=executor.is_active,
            is_done=executor.is_done,
            close_type=executor.close_type,
            state=custom.get("state"),
            position_address=custom.get("position_address"),
            base_amount=base_amount,
            quote_amount=quote_amount,
            base_fee=base_fee,
            quote_fee=quote_fee,
            lower_price=lower,
            upper_price=upper,
            current_price=price,
            out_of_range_since=out_of_range_since,
        )

    def _parse_lp_balance_event(self, executor: ExecutorInfo, now: float) -> Optional[BalanceEvent]:
        custom = executor.custom_info or {}
        balance_event = custom.get("balance_event")
        if not isinstance(balance_event, dict):
            return None
        kind = balance_event.get("type")
        try:
            kind_enum = BalanceEventKind(kind)
        except (TypeError, ValueError):
            return None
        if kind_enum not in {BalanceEventKind.LP_OPEN, BalanceEventKind.LP_CLOSE}:
            return None
        delta = balance_event.get("delta") or {}
        delta_base = self._to_decimal(delta.get("base")) or Decimal("0")
        delta_quote = self._to_decimal(delta.get("quote")) or Decimal("0")

        rent_delta = self._rent_delta(kind_enum, custom)
        if rent_delta != 0:
            native_token = self._config.native_token_symbol
            if native_token == self._domain.base_token:
                delta_base += rent_delta
            elif native_token == self._domain.quote_token:
                delta_quote += rent_delta

        timestamp = balance_event.get("timestamp")
        if timestamp is None or float(timestamp) <= 0:
            timestamp = executor.close_timestamp or executor.timestamp or now
        timestamp = float(timestamp)
        seq = balance_event.get("seq")
        event_id = f"{executor.id}:{seq}" if seq is not None else f"{executor.id}:{kind_enum.value}:{int(timestamp * 1000)}"
        return BalanceEvent(
            event_id=event_id,
            executor_id=executor.id,
            timestamp=timestamp,
            kind=kind_enum,
            delta_base=delta_base,
            delta_quote=delta_quote,
        )

    def _parse_swap_balance_event(self, executor: ExecutorInfo, now: float) -> Optional[BalanceEvent]:
        if not executor.is_done or executor.close_type != CloseType.COMPLETED:
            return None
        custom = executor.custom_info or {}
        delta_base = self._to_decimal(custom.get("delta_base"))
        delta_quote = self._to_decimal(custom.get("delta_quote"))
        if delta_base is None or delta_quote is None:
            return None
        timestamp = float(executor.close_timestamp or executor.timestamp or now)
        event_id = f"{executor.id}:{BalanceEventKind.SWAP.value}"
        return BalanceEvent(
            event_id=event_id,
            executor_id=executor.id,
            timestamp=timestamp,
            kind=BalanceEventKind.SWAP,
            delta_base=delta_base,
            delta_quote=delta_quote,
        )

    @staticmethod
    def _rent_delta(kind: BalanceEventKind, custom: Dict) -> Decimal:
        if kind == BalanceEventKind.LP_OPEN:
            rent = SnapshotBuilder._to_decimal(custom.get("position_rent")) or Decimal("0")
            return -rent
        if kind == BalanceEventKind.LP_CLOSE:
            rent = SnapshotBuilder._to_decimal(custom.get("position_rent_refunded")) or Decimal("0")
            return rent
        return Decimal("0")

    def _get_current_price(self) -> Optional[Decimal]:
        price = self._market_data_provider.get_rate(self._config.trading_pair)
        if price is not None:
            return Decimal(str(price))
        if self._pool_price_provider is None:
            return None
        pool_price = self._pool_price_provider()
        if pool_price is None:
            return None
        return Decimal(str(pool_price))

    @staticmethod
    def _to_decimal(value: Optional[object]) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _swap_purpose(level_id: Optional[str]) -> Optional[SwapPurpose]:
        if level_id == SwapPurpose.INVENTORY.value:
            return SwapPurpose.INVENTORY
        if level_id == SwapPurpose.STOPLOSS.value:
            return SwapPurpose.STOPLOSS
        return None


class BalanceManager:
    def __init__(
        self,
        *,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        market_data_provider,
        logger: Callable[[], HummingbotLogger],
    ) -> None:
        self._config = config
        self._domain = domain
        self._market_data_provider = market_data_provider
        self._logger = logger

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")
        self._last_balance_update_ts: float = 0.0
        self._last_balance_attempt_ts: float = 0.0
        self._wallet_update_task: Optional[asyncio.Task] = None
        self._has_balance_snapshot: bool = False
        self._logged_balance_snapshot: bool = False

    @property
    def wallet_base(self) -> Decimal:
        return self._wallet_base

    @property
    def wallet_quote(self) -> Decimal:
        return self._wallet_quote

    @property
    def has_snapshot(self) -> bool:
        return self._has_balance_snapshot

    def schedule_refresh(self, now: float) -> None:
        if self._wallet_update_task is not None and not self._wallet_update_task.done():
            return
        if (now - self._last_balance_attempt_ts) < 1.0:
            return
        if self._config.balance_refresh_interval_sec > 0:
            if self._has_balance_snapshot and (
                (now - self._last_balance_update_ts) < self._config.balance_refresh_interval_sec
            ):
                return

        connector = self._market_data_provider.connectors.get(self._config.connector_name)
        if connector is None:
            return
        self._last_balance_attempt_ts = now
        self._wallet_update_task = safe_ensure_future(self._update_wallet_balances(connector))
        self._wallet_update_task.add_done_callback(self._clear_wallet_update_task)

    def is_fresh(self, now: float) -> bool:
        if not self._has_balance_snapshot:
            return False
        timeout = max(0, self._config.balance_refresh_timeout_sec)
        if timeout <= 0:
            return True
        return (now - self._last_balance_update_ts) <= timeout

    def _clear_wallet_update_task(self, task: asyncio.Task) -> None:
        if self._wallet_update_task is task:
            self._wallet_update_task = None

    async def _update_wallet_balances(self, connector) -> None:
        try:
            timeout = float(max(1, self._config.balance_update_timeout_sec))
            await asyncio.wait_for(connector.update_balances(), timeout=timeout)
            self._wallet_base = Decimal(str(connector.get_available_balance(self._domain.base_token) or 0))
            self._wallet_quote = Decimal(str(connector.get_available_balance(self._domain.quote_token) or 0))
            self._last_balance_update_ts = self._market_data_provider.time()
            self._has_balance_snapshot = True
            if not self._logged_balance_snapshot:
                self._logger().info(
                    "balance_snapshot_ready | base=%s quote=%s",
                    self._wallet_base,
                    self._wallet_quote,
                )
                self._logged_balance_snapshot = True
        except Exception:
            self._logger().exception(
                "update_balances failed | connector=%s base=%s quote=%s last_update_ts=%.0f last_attempt_ts=%.0f",
                self._config.connector_name,
                self._domain.base_token,
                self._domain.quote_token,
                self._last_balance_update_ts,
            self._last_balance_attempt_ts,
        )


class PoolPriceManager:
    def __init__(
        self,
        *,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        market_data_provider,
        logger: Callable[[], HummingbotLogger],
    ) -> None:
        self._config = config
        self._domain = domain
        self._market_data_provider = market_data_provider
        self._logger = logger

        self._price: Optional[Decimal] = None
        self._last_price_ts: float = 0.0
        self._last_price_attempt_ts: float = 0.0
        self._price_update_task: Optional[asyncio.Task] = None

    def schedule_refresh(self, now: float) -> None:
        if self._price_update_task is not None and not self._price_update_task.done():
            return
        if (now - self._last_price_attempt_ts) < 1.0:
            return
        if self._config.balance_refresh_interval_sec > 0 and self._last_price_ts > 0:
            if (now - self._last_price_ts) < self._config.balance_refresh_interval_sec:
                return
        connector = self._market_data_provider.connectors.get(self._config.connector_name)
        if connector is None or not hasattr(connector, "get_pool_info"):
            return
        self._last_price_attempt_ts = now
        self._price_update_task = safe_ensure_future(self._update_price(connector))
        self._price_update_task.add_done_callback(self._clear_price_update_task)

    def get_price(self) -> Optional[Decimal]:
        if self._price is None or self._last_price_ts <= 0:
            return None
        timeout = max(0, self._config.balance_refresh_timeout_sec)
        if timeout > 0:
            now = self._market_data_provider.time()
            if (now - self._last_price_ts) > timeout:
                return None
        return self._price

    def _clear_price_update_task(self, task: asyncio.Task) -> None:
        if self._price_update_task is task:
            self._price_update_task = None

    async def _update_price(self, connector) -> None:
        pool_pair = self._config.pool_trading_pair or self._config.trading_pair
        try:
            timeout = float(max(1, self._config.balance_update_timeout_sec))
            pool_info = await asyncio.wait_for(connector.get_pool_info(pool_pair), timeout=timeout)
            if pool_info is None:
                return
            price_value = getattr(pool_info, "price", None)
            if price_value is None:
                return
            pool_price = Decimal(str(price_value))
            if pool_price <= 0:
                return
            pool_price = self._domain.pool_price_to_strategy(pool_price, self._domain.pool_order_inverted)
            if pool_price <= 0:
                return
            self._price = pool_price
            self._last_price_ts = self._market_data_provider.time()
        except Exception:
            self._logger().exception(
                "pool_price_update_failed | connector=%s pair=%s",
                self._config.connector_name,
                pool_pair,
            )


class ActionFactory:
    def __init__(
        self,
        *,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        budget_key: str,
        budget_coordinator,
        market_data_provider,
        extra_lp_params: Callable[[], Optional[Dict]],
    ) -> None:
        self._config = config
        self._domain = domain
        self._budget_key = budget_key
        self._budget_coordinator = budget_coordinator
        self._market_data_provider = market_data_provider
        self._extra_lp_params = extra_lp_params

    def build_open_lp_action(self, proposal: "OpenProposal", now: float) -> Optional[CreateExecutorAction]:
        executor_config = self._create_lp_executor_config(proposal, now)
        if executor_config is None:
            return None
        return CreateExecutorAction(controller_id=self._config.id, executor_config=executor_config)

    def swap_slippage_pct(self) -> Decimal:
        return max(Decimal("0"), self._config.swap_slippage_pct) * Decimal("100")

    def build_swap_action(
        self,
        *,
        level_id: str,
        now: float,
        side: TradeType,
        amount: Decimal,
        amount_in_is_quote: bool,
        apply_buffer: bool,
    ) -> Optional[CreateExecutorAction]:
        if amount <= 0:
            return None
        if apply_buffer:
            amount = self._apply_swap_buffer(amount)
            if amount <= 0:
                return None
        executor_config = GatewaySwapExecutorConfig(
            timestamp=now,
            connector_name=self._config.router_connector,
            trading_pair=self._config.trading_pair,
            side=side,
            amount=amount,
            amount_in_is_quote=amount_in_is_quote,
            slippage_pct=self.swap_slippage_pct(),
            pool_address=self._config.pool_address or None,
            level_id=level_id,
            budget_key=self._budget_key,
        )
        return CreateExecutorAction(
            controller_id=self._config.id,
            executor_config=executor_config,
        )

    def _apply_swap_buffer(self, amount: Decimal) -> Decimal:
        buffer_pct = max(Decimal("0"), self._config.swap_safety_buffer_pct)
        if buffer_pct <= 0:
            return amount
        if buffer_pct >= 1:
            return Decimal("0")
        return amount * (Decimal("1") - buffer_pct)

    def _create_lp_executor_config(self, proposal: "OpenProposal", now: float) -> Optional[LPPositionExecutorConfig]:
        if proposal.open_base <= 0 or proposal.open_quote <= 0:
            return None
        lower_price, upper_price = proposal.lower, proposal.upper
        lp_base_amt, lp_quote_amt = self._domain.strategy_amounts_to_pool(
            proposal.open_base,
            proposal.open_quote,
        )
        lp_lower_price, lp_upper_price = self._domain.strategy_bounds_to_pool(lower_price, upper_price)

        side = self._get_side_from_amounts(lp_base_amt, lp_quote_amt)
        executor_config = LPPositionExecutorConfig(
            timestamp=now,
            connector_name=self._config.connector_name,
            pool_address=self._config.pool_address,
            trading_pair=self._domain.pool_trading_pair,
            base_token=self._domain.pool_base_token,
            quote_token=self._domain.pool_quote_token,
            lower_price=lp_lower_price,
            upper_price=lp_upper_price,
            base_amount=lp_base_amt,
            quote_amount=lp_quote_amt,
            side=side,
            keep_position=False,
            budget_key=self._budget_key,
        )
        extra_params = self._extra_lp_params()
        if extra_params:
            executor_config.extra_params = extra_params
        reservation_id = self._reserve_budget(proposal.open_base, proposal.open_quote)
        if reservation_id is None:
            return None
        executor_config.budget_reservation_id = reservation_id
        return executor_config

    @staticmethod
    def _get_side_from_amounts(base_amt: Decimal, quote_amt: Decimal) -> int:
        if base_amt > 0 and quote_amt > 0:
            return 0
        if quote_amt > 0:
            return 1
        return 2

    def _reserve_budget(self, base_amt: Decimal, quote_amt: Decimal) -> Optional[str]:
        connector = self._market_data_provider.connectors.get(self._config.connector_name)
        if connector is None:
            return None
        requirements: Dict[str, Decimal] = {}
        if base_amt > 0:
            requirements[self._domain.base_token] = base_amt
        if quote_amt > 0:
            requirements[self._domain.quote_token] = quote_amt
        reservation_id = self._budget_coordinator.reserve(
            connector_name=self._config.connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self._config.native_token_symbol,
            min_native_balance=self._config.min_native_balance,
        )
        return reservation_id
