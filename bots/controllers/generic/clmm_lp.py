import logging
from collections import deque
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, List, Optional, Set, Tuple

from pydantic import Field

from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.budget.budget_coordinator import BudgetCoordinatorRegistry
from hummingbot.strategy_v2.budget.fixed_budget_pool import FixedBudgetPoolRegistry
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig, LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


class BudgetMode(str, Enum):
    WALLET = "wallet"
    FIXED = "fixed"


class StopLossLiquidationMode(str, Enum):
    NONE = "none"
    QUOTE = "quote"


class ControllerState(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    REBALANCE_WAIT_CLOSE = "REBALANCE_WAIT_CLOSE"
    INVENTORY_SWAP = "INVENTORY_SWAP"
    READY_TO_OPEN = "READY_TO_OPEN"
    WAIT_SWAP = "WAIT_SWAP"
    STOPLOSS_PAUSE = "STOPLOSS_PAUSE"
    MANUAL_STOP = "MANUAL_STOP"


class CLMMLPGuardedControllerConfig(ControllerConfigBase):
    controller_type: str = "generic"
    controller_name: str = "clmm_lp"
    candles_config: List[CandlesConfig] = []

    connector_name: str = "meteora/clmm"
    router_connector: str = "jupiter/router"
    trading_pair: str = "SOL-USDC"
    pool_address: str = ""

    target_price: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    trigger_above: bool = Field(default=True, json_schema_extra={"is_updatable": True})

    base_amount: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    quote_amount: Decimal = Field(default=Decimal("0.2"), json_schema_extra={"is_updatable": True})

    position_width_pct: Decimal = Field(default=Decimal("12"), json_schema_extra={"is_updatable": True})
    rebalance_seconds: int = Field(default=60, json_schema_extra={"is_updatable": True})
    hysteresis_pct: Decimal = Field(default=Decimal("0.20"), json_schema_extra={"is_updatable": True})
    cooldown_seconds: int = Field(default=30, json_schema_extra={"is_updatable": True})
    max_rebalances_per_hour: int = Field(default=20, json_schema_extra={"is_updatable": True})
    reopen_delay_sec: int = Field(default=5, json_schema_extra={"is_updatable": True})

    auto_swap_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})
    target_base_value_pct: Decimal = Field(default=Decimal("0.5"), json_schema_extra={"is_updatable": True})
    swap_min_quote_value: Decimal = Field(default=Decimal("0.01"), json_schema_extra={"is_updatable": True})
    swap_safety_buffer_pct: Decimal = Field(default=Decimal("2"), json_schema_extra={"is_updatable": True})
    swap_timeout_sec: int = Field(default=120, json_schema_extra={"is_updatable": True})
    swap_poll_interval_sec: Decimal = Field(default=Decimal("2"), json_schema_extra={"is_updatable": True})
    swap_slippage_pct: Decimal = Field(default=Decimal("1"), json_schema_extra={"is_updatable": True})
    swap_retry_attempts: int = Field(default=0, json_schema_extra={"is_updatable": True})
    swap_retry_delay_sec: Decimal = Field(default=Decimal("1"), json_schema_extra={"is_updatable": True})

    stop_loss_pnl_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    stop_loss_pause_sec: int = Field(default=1800, json_schema_extra={"is_updatable": True})
    stop_loss_liquidation_mode: StopLossLiquidationMode = Field(
        default=StopLossLiquidationMode.QUOTE,
        json_schema_extra={"is_updatable": True},
    )
    reenter_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})

    budget_key: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    budget_mode: BudgetMode = Field(default=BudgetMode.WALLET, json_schema_extra={"is_updatable": True})
    fixed_budget_base: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    fixed_budget_quote: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    native_token_symbol: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    min_native_balance: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets = markets.add_or_update(self.connector_name, self.trading_pair)
        markets = markets.add_or_update(self.router_connector, self.trading_pair)
        return markets


class CLMMLPGuardedController(ControllerBase):
    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: CLMMLPGuardedControllerConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: CLMMLPGuardedControllerConfig = config

        parts = config.trading_pair.split("-")
        self._base_token = parts[0] if len(parts) >= 2 else ""
        self._quote_token = parts[1] if len(parts) >= 2 else ""

        self._budget_key = self.config.budget_key or self.config.id
        self._budget_coordinator = BudgetCoordinatorRegistry.get(self._budget_key)
        self._budget_mode = self.config.budget_mode
        self._budget_pool = None
        self._pool_reservations: Dict[str, str] = {}
        self._settled_pool_executors: Set[str] = set()
        self._swap_pool_reservations: Dict[str, str] = {}
        self._settled_swap_executors: Set[str] = set()
        self._stop_loss_liquidation_mode = self.config.stop_loss_liquidation_mode

        self._state: ControllerState = ControllerState.IDLE
        self._last_rebalance_ts: float = 0.0
        self._rebalance_timestamps: Deque[float] = deque(maxlen=200)
        self._reopen_after_ts: float = 0.0
        self._pending_rebalance_info: Optional[Dict] = None
        self._pending_liquidation: bool = False
        self._inventory_swap_attempted: bool = False
        self._inventory_swap_failed: Optional[bool] = None
        self._stop_loss_until_ts: float = 0.0
        self._last_exit_reason: Optional[str] = None
        self._anchor_value_by_executor: Dict[str, Decimal] = {}

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")

        self._initialize_budget_pool()

        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=self.config.connector_name,
                trading_pair=self.config.trading_pair,
            ),
            ConnectorPair(
                connector_name=self.config.router_connector,
                trading_pair=self.config.trading_pair,
            ),
        ])

    async def update_processed_data(self):
        await self._update_wallet_balances()
        budget_snapshot = self._budget_pool.snapshot() if self._budget_pool else None
        self.processed_data = {
            "current_price": self._get_current_price(),
            "wallet_base": self._wallet_base,
            "wallet_quote": self._wallet_quote,
            "controller_state": self._state.value,
            "budget_pool": budget_snapshot,
        }

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions: List[ExecutorAction] = []
        now = self.market_data_provider.time()

        self._reconcile_budget_pool()

        lp_executor = self._get_active_lp_executor()
        swap_executor = self._get_active_swap_executor()

        if self.config.manual_kill_switch:
            self._set_state(ControllerState.MANUAL_STOP, "manual_kill_switch")
            if lp_executor:
                actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=lp_executor.id))
            return actions

        if swap_executor:
            swap_label = swap_executor.config.level_id or "swap"
            self._set_state(ControllerState.WAIT_SWAP, f"{swap_label}_in_progress")
            return actions

        if self._pending_liquidation:
            self._set_state(ControllerState.WAIT_SWAP, "stop_loss_liquidation")
            swap_action = self._build_liquidation_action()
            if swap_action:
                actions.append(swap_action)
                self._pending_liquidation = False
            return actions

        if lp_executor:
            self._set_state(ControllerState.ACTIVE)
            stop_action = self._maybe_stop_for_stop_loss(lp_executor, now)
            if stop_action:
                self._set_state(ControllerState.STOPLOSS_PAUSE, "stop_loss_triggered")
                actions.append(stop_action)
                return actions

            stop_action = self._maybe_stop_for_rebalance(lp_executor, now)
            if stop_action:
                self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "out_of_range_rebalance")
                actions.append(stop_action)
                return actions

            return actions

        if now < self._stop_loss_until_ts:
            self._set_state(ControllerState.STOPLOSS_PAUSE, "cooldown")
            return actions

        if self._pending_rebalance_info is not None:
            if now < self._reopen_after_ts:
                self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "reopen_delay")
                return actions

            if self.config.auto_swap_enabled and not self._inventory_swap_attempted and not self._should_use_single_sided_rebalance():
                swap_action = self._build_inventory_swap_action()
                if swap_action:
                    self._set_state(ControllerState.INVENTORY_SWAP, "rebalance_inventory")
                    actions.append(swap_action)
                    return actions

            use_single_sided = self._should_use_single_sided_rebalance()
            self._set_state(ControllerState.READY_TO_OPEN, "rebalance_open")
            lp_action = self._create_lp_executor_action(use_single_sided=use_single_sided)
            if lp_action:
                actions.append(lp_action)
                self._set_state(ControllerState.ACTIVE, "lp_open")
            else:
                self._set_state(ControllerState.IDLE, "lp_open_failed")
            return actions

        if not self._can_start_new_position(now):
            self._set_state(ControllerState.IDLE, "entry_blocked")
            return actions

        if self.config.auto_swap_enabled and not self._inventory_swap_attempted:
            swap_action = self._build_inventory_swap_action()
            if swap_action:
                self._set_state(ControllerState.INVENTORY_SWAP, "entry_inventory")
                actions.append(swap_action)
                return actions

        self._set_state(ControllerState.READY_TO_OPEN, "entry_open")
        lp_action = self._create_lp_executor_action(use_single_sided=False)
        if lp_action:
            actions.append(lp_action)
            self._set_state(ControllerState.ACTIVE, "lp_open")
        else:
            self._set_state(ControllerState.IDLE, "lp_open_failed")
        return actions

    def _get_active_lp_executor(self) -> Optional[ExecutorInfo]:
        active = [e for e in self.executors_info if e.is_active and e.type == "lp_position_executor"]
        return active[0] if active else None

    def _get_active_swap_executor(self) -> Optional[ExecutorInfo]:
        active = [e for e in self.executors_info if e.is_active and e.type == "gateway_swap_executor"]
        return active[0] if active else None

    def _get_current_price(self) -> Optional[Decimal]:
        price = self.market_data_provider.get_rate(self.config.trading_pair)
        if price is None:
            return None
        try:
            return Decimal(str(price))
        except Exception:
            return None

    async def _update_wallet_balances(self):
        connector = self.market_data_provider.connectors.get(self.config.router_connector)
        if connector is None:
            return
        try:
            await connector.update_balances(on_interval=False)
            self._wallet_base = Decimal(str(connector.get_balance(self._base_token) or 0))
            self._wallet_quote = Decimal(str(connector.get_balance(self._quote_token) or 0))
        except Exception:
            return

    def _can_start_new_position(self, now: float) -> bool:
        if not self._is_entry_triggered():
            return False
        if not self.config.reenter_enabled and self._last_exit_reason == "stop_loss":
            return False
        if now < self._stop_loss_until_ts:
            return False
        if self._pending_rebalance_info is not None and now < self._reopen_after_ts:
            return False
        return True

    def _is_entry_triggered(self) -> bool:
        if self.config.target_price <= 0:
            return True
        current_price = self._get_current_price()
        if current_price is None:
            return False
        if self.config.trigger_above:
            return current_price >= self.config.target_price
        return current_price <= self.config.target_price

    def _maybe_stop_for_stop_loss(self, executor: ExecutorInfo, now: float) -> Optional[StopExecutorAction]:
        if self.config.stop_loss_pnl_pct <= 0:
            return None
        anchor_value = self._anchor_value_by_executor.get(executor.id)
        if anchor_value is None or anchor_value <= 0:
            return None
        trigger_loss = anchor_value * self.config.stop_loss_pnl_pct
        if executor.net_pnl_quote <= -trigger_loss:
            self._last_exit_reason = "stop_loss"
            self._stop_loss_until_ts = now + self.config.stop_loss_pause_sec
            if self._stop_loss_liquidation_mode == StopLossLiquidationMode.QUOTE:
                self._pending_liquidation = True
            self._reset_inventory_swap_state()
            self._clear_rebalance_context()
            return StopExecutorAction(controller_id=self.config.id, executor_id=executor.id)
        return None

    def _maybe_stop_for_rebalance(self, executor: ExecutorInfo, now: float) -> Optional[StopExecutorAction]:
        if self._pending_rebalance_info is not None:
            return None
        custom = executor.custom_info
        state = custom.get("state")
        if state in [LPPositionStates.OPENING.value, LPPositionStates.CLOSING.value]:
            return None
        if state != LPPositionStates.OUT_OF_RANGE.value:
            return None

        current_price = custom.get("current_price")
        lower_price = custom.get("lower_price")
        upper_price = custom.get("upper_price")
        if current_price is None or lower_price is None or upper_price is None:
            return None

        current_price = Decimal(str(current_price))
        lower_price = Decimal(str(lower_price))
        upper_price = Decimal(str(upper_price))

        deviation_pct = self._out_of_range_deviation_pct(current_price, lower_price, upper_price)
        if deviation_pct < self.config.hysteresis_pct:
            return None

        out_of_range_since = custom.get("out_of_range_since")
        if out_of_range_since is None:
            return None
        if (now - out_of_range_since) < self.config.rebalance_seconds:
            return None
        if (now - self._last_rebalance_ts) < self.config.cooldown_seconds:
            return None
        if not self._can_rebalance_now(now):
            return None

        self._pending_rebalance_info = custom.copy()
        self._reopen_after_ts = now + self.config.reopen_delay_sec
        self._record_rebalance(now)
        self._reset_inventory_swap_state()
        return StopExecutorAction(controller_id=self.config.id, executor_id=executor.id)

    def _record_rebalance(self, now: float):
        self._last_rebalance_ts = now
        self._rebalance_timestamps.append(now)

    def _can_rebalance_now(self, now: float) -> bool:
        if self.config.max_rebalances_per_hour <= 0:
            return True
        while self._rebalance_timestamps and (now - self._rebalance_timestamps[0] > 3600):
            self._rebalance_timestamps.popleft()
        return len(self._rebalance_timestamps) < self.config.max_rebalances_per_hour

    def _out_of_range_deviation_pct(self, price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        if price < lower:
            return (lower - price) / lower * Decimal("100")
        if price > upper:
            return (price - upper) / upper * Decimal("100")
        return Decimal("0")

    def _build_liquidation_action(self) -> Optional[CreateExecutorAction]:
        base_amount, _ = self._get_budget_balances()
        if base_amount <= 0:
            return None
        swap_amount = self._apply_swap_buffer(base_amount)
        if swap_amount <= 0:
            return None
        executor_config = GatewaySwapExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.router_connector,
            trading_pair=self.config.trading_pair,
            side=TradeType.SELL,
            amount=swap_amount,
            amount_in_is_quote=False,
            slippage_pct=self.config.swap_slippage_pct,
            pool_address=self.config.pool_address or None,
            timeout_sec=self.config.swap_timeout_sec,
            poll_interval_sec=self.config.swap_poll_interval_sec,
            max_retries=self.config.swap_retry_attempts,
            retry_delay_sec=self.config.swap_retry_delay_sec,
            level_id="liquidate",
            budget_key=self._budget_key,
        )
        self._record_swap_adjustment(executor_config)
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _build_inventory_swap_action(self) -> Optional[CreateExecutorAction]:
        executor_config = self._calculate_inventory_swap_config()
        if executor_config is None:
            return None
        self._record_swap_adjustment(executor_config)
        self._inventory_swap_attempted = True
        self._inventory_swap_failed = None
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _calculate_inventory_swap_config(self) -> Optional[GatewaySwapExecutorConfig]:
        price = self._get_current_price()
        if price is None or price <= 0:
            return None

        base_balance, quote_balance = self._get_budget_balances()
        total_value = base_balance * price + quote_balance
        if total_value <= 0:
            return None

        target_ratio = self._normalized_target_ratio()
        base_target_value = total_value * target_ratio
        base_target_amount = base_target_value / price if price > 0 else Decimal("0")
        delta_base = base_target_amount - base_balance
        delta_quote_value = abs(delta_base * price)

        if delta_quote_value < self.config.swap_min_quote_value:
            return None

        if delta_base > 0:
            quote_amount = min(quote_balance, self._apply_swap_buffer(delta_quote_value))
            if quote_amount <= 0:
                return None
            return GatewaySwapExecutorConfig(
                timestamp=self.market_data_provider.time(),
                connector_name=self.config.router_connector,
                trading_pair=self.config.trading_pair,
                side=TradeType.BUY,
                amount=quote_amount,
                amount_in_is_quote=True,
                slippage_pct=self.config.swap_slippage_pct,
                pool_address=self.config.pool_address or None,
                timeout_sec=self.config.swap_timeout_sec,
                poll_interval_sec=self.config.swap_poll_interval_sec,
                max_retries=self.config.swap_retry_attempts,
                retry_delay_sec=self.config.swap_retry_delay_sec,
                level_id="auto_swap",
                budget_key=self._budget_key,
            )

        base_amount = min(base_balance, self._apply_swap_buffer(abs(delta_base)))
        if base_amount <= 0:
            return None
        return GatewaySwapExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.router_connector,
            trading_pair=self.config.trading_pair,
            side=TradeType.SELL,
            amount=base_amount,
            amount_in_is_quote=False,
            slippage_pct=self.config.swap_slippage_pct,
            pool_address=self.config.pool_address or None,
            timeout_sec=self.config.swap_timeout_sec,
            poll_interval_sec=self.config.swap_poll_interval_sec,
            max_retries=self.config.swap_retry_attempts,
            retry_delay_sec=self.config.swap_retry_delay_sec,
            level_id="auto_swap",
            budget_key=self._budget_key,
        )

    def _apply_swap_buffer(self, amount: Decimal) -> Decimal:
        buffer_pct = max(Decimal("0"), self.config.swap_safety_buffer_pct)
        if buffer_pct <= 0:
            return amount
        return amount * (Decimal("1") - (buffer_pct / Decimal("100")))

    def _normalized_target_ratio(self) -> Decimal:
        ratio = self.config.target_base_value_pct
        if ratio > 1:
            ratio = ratio / Decimal("100")
        if ratio < 0:
            return Decimal("0")
        if ratio > 1:
            return Decimal("1")
        return ratio

    def _calculate_target_allocation_amounts(self, price: Decimal) -> Tuple[Decimal, Decimal]:
        base_available, quote_available = self._get_budget_balances()
        ratio = self._normalized_target_ratio()
        if ratio <= 0:
            return Decimal("0"), quote_available
        if ratio >= 1:
            return base_available, Decimal("0")
        base_value_cap = base_available * price
        quote_value_cap = quote_available
        if base_value_cap <= 0 or quote_value_cap <= 0:
            return base_available, quote_available
        total_value_by_base = base_value_cap / ratio
        total_value_by_quote = quote_value_cap / (Decimal("1") - ratio)
        total_value = min(total_value_by_base, total_value_by_quote)
        if total_value <= 0:
            return Decimal("0"), Decimal("0")
        base_amt = (total_value * ratio) / price
        quote_amt = total_value * (Decimal("1") - ratio)
        return base_amt, quote_amt

    def _should_use_single_sided_rebalance(self) -> bool:
        if self._pending_rebalance_info is None:
            return False
        if not self.config.auto_swap_enabled:
            return True
        return self._inventory_swap_failed is True

    def _create_lp_executor_action(self, use_single_sided: bool) -> Optional[CreateExecutorAction]:
        executor_config = self._create_lp_executor_config(use_single_sided=use_single_sided)
        if executor_config is None:
            return None
        self._anchor_value_by_executor[executor_config.id] = self._anchor_value_from_config(executor_config)
        self._clear_rebalance_context()
        self._reset_inventory_swap_state()
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _create_lp_executor_config(self, use_single_sided: bool) -> Optional[LPPositionExecutorConfig]:
        current_price = self._get_current_price()
        if current_price is None or current_price <= 0:
            return None

        if self._pending_rebalance_info and use_single_sided:
            info = self._pending_rebalance_info
            lower_price = Decimal(str(info.get("lower_price", current_price)))
            was_below_range = current_price < lower_price
            if was_below_range:
                base_amt = Decimal(str(info.get("base_amount", 0))) + Decimal(str(info.get("base_fee", 0)))
                quote_amt = Decimal("0")
            else:
                base_amt = Decimal("0")
                quote_amt = Decimal(str(info.get("quote_amount", 0))) + Decimal(str(info.get("quote_fee", 0)))
        else:
            if self._budget_pool:
                base_amt, quote_amt = self._calculate_target_allocation_amounts(current_price)
            else:
                base_amt = self.config.base_amount
                quote_amt = self.config.quote_amount

        if base_amt <= 0 and quote_amt <= 0:
            return None

        lower_price, upper_price = self._calculate_price_bounds(current_price, base_amt, quote_amt)

        side = self._get_side_from_amounts(base_amt, quote_amt)
        executor_config = LPPositionExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            pool_address=self.config.pool_address,
            trading_pair=self.config.trading_pair,
            base_token=self._base_token,
            quote_token=self._quote_token,
            lower_price=lower_price,
            upper_price=upper_price,
            base_amount=base_amt,
            quote_amount=quote_amt,
            side=side,
            keep_position=False,
            budget_key=self._budget_key,
        )
        reservation_id = self._reserve_budget(base_amt, quote_amt, executor_config.id)
        if reservation_id is None:
            return None
        executor_config.budget_reservation_id = reservation_id
        return executor_config

    def _calculate_price_bounds(
        self,
        current_price: Decimal,
        base_amt: Decimal,
        quote_amt: Decimal,
    ) -> tuple[Decimal, Decimal]:
        total_width = self.config.position_width_pct / Decimal("100")
        if base_amt > 0 and quote_amt > 0:
            half_width = total_width / Decimal("2")
            lower_price = current_price * (Decimal("1") - half_width)
            upper_price = current_price * (Decimal("1") + half_width)
        elif base_amt > 0:
            lower_price = current_price
            upper_price = current_price * (Decimal("1") + total_width)
        elif quote_amt > 0:
            lower_price = current_price * (Decimal("1") - total_width)
            upper_price = current_price
        else:
            half_width = total_width / Decimal("2")
            lower_price = current_price * (Decimal("1") - half_width)
            upper_price = current_price * (Decimal("1") + half_width)
        return lower_price, upper_price

    def _get_side_from_amounts(self, base_amt: Decimal, quote_amt: Decimal) -> int:
        if base_amt > 0 and quote_amt > 0:
            return 0
        if quote_amt > 0:
            return 1
        return 2

    def _anchor_value_from_config(self, config: LPPositionExecutorConfig) -> Decimal:
        price = self._get_current_price()
        if price is None:
            return Decimal("0")
        return config.base_amount * price + config.quote_amount

    def _reserve_budget(self, base_amt: Decimal, quote_amt: Decimal, config_id: Optional[str]) -> Optional[str]:
        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is None:
            return None
        requirements = {}
        if base_amt > 0:
            requirements[self._base_token] = base_amt
        if quote_amt > 0:
            requirements[self._quote_token] = quote_amt
        pool_reservation_id = None
        if self._budget_pool:
            pool_reservation_id = self._budget_pool.reserve(requirements)
            if pool_reservation_id is None:
                return None
        reservation_id = self._budget_coordinator.reserve(
            connector_name=self.config.connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self.config.native_token_symbol,
            min_native_balance=self.config.min_native_balance,
        )
        if reservation_id is None and pool_reservation_id:
            self._budget_pool.release(pool_reservation_id)
            return None
        if pool_reservation_id and config_id:
            self._pool_reservations[config_id] = pool_reservation_id
        return reservation_id

    def _reserve_swap_budget(self, config: GatewaySwapExecutorConfig) -> Optional[str]:
        if not self._budget_pool:
            return None
        token_in, amount_in = self._get_swap_input_amount(config)
        if token_in is None or amount_in <= 0:
            return None
        return self._budget_pool.reserve({token_in: amount_in})

    def _get_swap_input_amount(self, config: GatewaySwapExecutorConfig) -> Tuple[Optional[str], Decimal]:
        if config.amount <= 0:
            return None, Decimal("0")
        base_token, quote_token = config.trading_pair.split("-")
        if config.amount_in_is_quote:
            return quote_token, config.amount
        if config.side == TradeType.SELL:
            return base_token, config.amount
        price = self._get_current_price()
        if price is None or price <= 0:
            return None, Decimal("0")
        return quote_token, config.amount * price

    def _set_state(self, state: ControllerState, reason: Optional[str] = None):
        if state == self._state:
            return
        previous = self._state
        self._state = state
        message = f"Controller state change: {previous.value} -> {state.value}"
        if reason:
            message = f"{message} ({reason})"
        self.logger().info(message)

    def _clear_rebalance_context(self):
        self._pending_rebalance_info = None
        self._reopen_after_ts = 0.0

    def _reset_inventory_swap_state(self):
        self._inventory_swap_attempted = False
        self._inventory_swap_failed = None

    def _get_budget_balances(self) -> Tuple[Decimal, Decimal]:
        if self._budget_pool:
            return (
                self._budget_pool.available(self._base_token),
                self._budget_pool.available(self._quote_token),
            )
        return self._wallet_base, self._wallet_quote

    def _initialize_budget_pool(self):
        if self._budget_mode != BudgetMode.FIXED:
            return
        quote_budget = self.config.fixed_budget_quote
        if quote_budget <= 0 and self.config.total_amount_quote > 0:
            quote_budget = self.config.total_amount_quote
        base_budget = self.config.fixed_budget_base
        if base_budget <= 0 and quote_budget <= 0:
            self.logger().warning(
                "Fixed budget mode enabled but budget amounts are zero; falling back to wallet mode."
            )
            self._budget_mode = BudgetMode.WALLET
            return
        self._budget_pool = FixedBudgetPoolRegistry.get(
            key=self._budget_key,
            base_token=self._base_token,
            quote_token=self._quote_token,
            base_budget=base_budget,
            quote_budget=quote_budget,
        )

    def _reconcile_budget_pool(self):
        for executor in self.executors_info:
            if executor.type == "gateway_swap_executor":
                self._maybe_settle_swap_adjustment(executor)

        if not self._budget_pool:
            return
        for executor in self.executors_info:
            if executor.type != "lp_position_executor":
                continue
            if executor.controller_id != self.config.id:
                continue
            if not executor.is_done:
                continue
            if executor.id in self._settled_pool_executors:
                continue
            self._settled_pool_executors.add(executor.id)
            pool_reservation_id = self._pool_reservations.get(executor.config.id)
            if not pool_reservation_id:
                continue
            if executor.close_type == CloseType.FAILED:
                self.logger().warning(
                    "LP executor failed; leaving budget pool reservation locked for manual review."
                )
                continue
            custom = executor.custom_info or {}
            base_amount = Decimal(str(custom.get("base_amount", 0)))
            quote_amount = Decimal(str(custom.get("quote_amount", 0)))
            base_fee = Decimal(str(custom.get("base_fee", 0)))
            quote_fee = Decimal(str(custom.get("quote_fee", 0)))
            returned = {
                self._base_token: base_amount + base_fee,
                self._quote_token: quote_amount + quote_fee,
            }
            self._budget_pool.settle(pool_reservation_id, returned)
            self._pool_reservations.pop(executor.config.id, None)

    def _record_swap_adjustment(self, config: GatewaySwapExecutorConfig):
        if not self._budget_pool:
            return
        if config.id in self._swap_pool_reservations:
            return
        reservation_id = self._reserve_swap_budget(config)
        if reservation_id:
            self._swap_pool_reservations[config.id] = reservation_id

    def _maybe_settle_swap_adjustment(self, executor: ExecutorInfo):
        if executor.controller_id != self.config.id:
            return
        if not executor.is_done:
            return
        if executor.id in self._settled_swap_executors:
            return
        self._settled_swap_executors.add(executor.id)
        if executor.config.level_id == "auto_swap":
            self._inventory_swap_attempted = True
            self._inventory_swap_failed = executor.close_type == CloseType.FAILED
        if not self._budget_pool:
            return
        reservation_id = self._swap_pool_reservations.pop(executor.config.id, None)
        if not reservation_id:
            return
        if executor.close_type == CloseType.FAILED:
            self._budget_pool.release(reservation_id)
            return
        custom = executor.custom_info or {}
        token_out = custom.get("token_out")
        amount_out = Decimal(str(custom.get("amount_out", 0)))
        if not token_out or amount_out <= 0:
            self.logger().warning("Swap completed without amount_out; releasing budget reservation.")
            self._budget_pool.release(reservation_id)
            return
        self._budget_pool.settle(reservation_id, {token_out: amount_out})
