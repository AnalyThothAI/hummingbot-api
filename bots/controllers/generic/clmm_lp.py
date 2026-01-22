import logging
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, List, Optional, Set, Tuple

from pydantic import Field, field_validator
from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.budget.budget_coordinator import BudgetCoordinatorRegistry
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig, LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


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
    LP_FAILURE = "LP_FAILURE"


class RebalanceStage(str, Enum):
    WAIT_REOPEN = "WAIT_REOPEN"
    SWAP_PENDING = "SWAP_PENDING"
    READY_TO_OPEN = "READY_TO_OPEN"


@dataclass
class RebalancePlan:
    info: Dict
    reopen_after_ts: float
    stage: RebalanceStage = RebalanceStage.WAIT_REOPEN
    swap_attempted: bool = False
    swap_failed: Optional[bool] = None
    swap_last_ts: float = 0.0


@dataclass
class PositionBudget:
    total_value_quote: Decimal
    target_base: Decimal
    target_quote: Decimal


@dataclass
class BudgetSnapshot:
    total_value_quote: Decimal
    base_amount: Decimal
    quote_amount: Decimal
    scale: Decimal


@dataclass
class BudgetAnchor:
    value_quote: Decimal
    wallet_base_amount: Decimal
    wallet_quote_amount: Decimal


@dataclass
class BudgetContext:
    budget: PositionBudget
    snapshot: BudgetSnapshot
    delta_base: Decimal
    delta_quote_value: Decimal
    swap_required: bool


# Fixed cost-filter constants to keep behavior deterministic and avoid parameter sprawl.
COST_FILTER_FEE_EWMA_ALPHA = Decimal("0.1")
COST_FILTER_FEE_SAMPLE_MIN_SECONDS = Decimal("10")
COST_FILTER_IN_RANGE_TIME_SEC = Decimal("3600")
COST_FILTER_SWAP_NOTIONAL_PCT = Decimal("0.5")
COST_FILTER_SWAP_FEE_BUFFER_PCT = Decimal("0.3")
COST_FILTER_FEE_RATE_FLOOR = Decimal("0.000000001")
COST_FILTER_SAFETY_FACTOR = Decimal("2")
COST_FILTER_FORCE_REBALANCE_MULTIPLIER = 10
COST_FILTER_FORCE_REBALANCE_MIN_SEC = 600


def evaluate_cost_filter(
    *,
    enabled: bool,
    current_price: Decimal,
    position_value: Decimal,
    fee_rate_ewma: Optional[Decimal],
    fee_rate_bootstrap_quote_per_hour: Decimal,
    position_width_pct: Decimal,
    auto_swap_enabled: bool,
    swap_slippage_pct: Decimal,
    fixed_cost_quote: Decimal,
    max_payback_sec: int,
) -> Tuple[bool, Dict[str, object]]:
    details: Dict[str, object] = {}
    if not enabled:
        details["reason"] = "disabled"
        return True, details
    if current_price <= 0:
        details["reason"] = "invalid_price"
        return False, details

    fee_rate = fee_rate_ewma
    fee_rate_source = "ewma"
    if fee_rate is None or fee_rate <= 0:
        fee_rate = fee_rate_bootstrap_quote_per_hour / Decimal("3600")
        fee_rate_source = "bootstrap" if fee_rate > 0 else "zero"

    half_width = (position_width_pct / Decimal("100")) / Decimal("2")
    in_range_time = COST_FILTER_IN_RANGE_TIME_SEC

    expected_fees = fee_rate * in_range_time
    fixed_cost = max(Decimal("0"), fixed_cost_quote)
    if auto_swap_enabled:
        swap_notional_pct = COST_FILTER_SWAP_NOTIONAL_PCT
    else:
        swap_notional_pct = Decimal("0")
    swap_notional = position_value * swap_notional_pct
    swap_fee_pct = max(Decimal("0"), swap_slippage_pct + COST_FILTER_SWAP_FEE_BUFFER_PCT)
    swap_cost = swap_notional * (swap_fee_pct / Decimal("100"))
    cost = fixed_cost + swap_cost

    details.update({
        "fee_rate": fee_rate,
        "fee_rate_source": fee_rate_source,
        "in_range_time": in_range_time,
        "in_range_source": "fixed",
        "lower_width": half_width,
        "upper_width": half_width,
        "expected_fees": expected_fees,
        "position_value": position_value,
        "fixed_cost": fixed_cost,
        "swap_notional": swap_notional,
        "swap_cost": swap_cost,
        "cost": cost,
    })
    if cost <= 0:
        details["reason"] = "zero_cost"
        return True, details

    if expected_fees < (cost * COST_FILTER_SAFETY_FACTOR):
        details["reason"] = "fee_rate_zero" if fee_rate <= 0 else "expected_fee_below_threshold"
        return False, details

    min_fee_rate = max(Decimal("0"), COST_FILTER_FEE_RATE_FLOOR)
    payback = cost / max(fee_rate, min_fee_rate)
    details["payback_sec"] = payback
    details["max_payback_sec"] = Decimal(str(max_payback_sec))
    if payback > Decimal(str(max_payback_sec)):
        details["reason"] = "payback_exceeded"
        return False, details
    details["reason"] = "approved"
    return True, details


def should_force_rebalance(now: float, out_of_range_since: float, rebalance_seconds: int) -> bool:
    if rebalance_seconds <= 0:
        return False
    threshold = max(
        rebalance_seconds * COST_FILTER_FORCE_REBALANCE_MULTIPLIER,
        COST_FILTER_FORCE_REBALANCE_MIN_SEC,
    )
    return (now - out_of_range_since) >= threshold


def apply_inventory_skew_to_widths(
    *,
    total_width: Decimal,
    skew: Decimal,
    min_width: Decimal,
) -> Tuple[Decimal, Decimal]:
    half_width = total_width / Decimal("2")
    upper_width = half_width * (Decimal("1") - skew)
    lower_width = half_width * (Decimal("1") + skew)

    min_width = min(min_width, half_width)
    if upper_width < min_width:
        upper_width = min_width
        lower_width = total_width - upper_width
    elif lower_width < min_width:
        lower_width = min_width
        upper_width = total_width - lower_width

    if upper_width < min_width or lower_width < min_width:
        upper_width = half_width
        lower_width = half_width
    return lower_width, upper_width


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

    position_value_quote: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})

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

    cost_filter_enabled: bool = Field(default=False, json_schema_extra={"is_updatable": True})
    cost_filter_fee_rate_bootstrap_quote_per_hour: Decimal = Field(
        default=Decimal("0"),
        json_schema_extra={"is_updatable": True},
    )
    cost_filter_fixed_cost_quote: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    cost_filter_max_payback_sec: int = Field(default=3600, json_schema_extra={"is_updatable": True})

    inventory_skew_enabled: bool = Field(default=False, json_schema_extra={"is_updatable": True})
    inventory_skew_k: Decimal = Field(default=Decimal("2"), json_schema_extra={"is_updatable": True})
    inventory_skew_max: Decimal = Field(default=Decimal("0.6"), json_schema_extra={"is_updatable": True})
    inventory_skew_ema_alpha: Decimal = Field(default=Decimal("0.1"), json_schema_extra={"is_updatable": True})
    inventory_skew_step_min: Decimal = Field(default=Decimal("0.05"), json_schema_extra={"is_updatable": True})
    inventory_skew_min_width_pct: Decimal = Field(default=Decimal("0.5"), json_schema_extra={"is_updatable": True})
    inventory_soft_band_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    inventory_hard_band_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})

    stop_loss_pnl_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    stop_loss_pause_sec: int = Field(default=1800, json_schema_extra={"is_updatable": True})
    stop_loss_liquidation_mode: StopLossLiquidationMode = Field(
        default=StopLossLiquidationMode.QUOTE,
        json_schema_extra={"is_updatable": True},
    )
    reenter_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})

    budget_key: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    native_token_symbol: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    min_native_balance: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})

    @field_validator("position_value_quote", mode="before")
    @classmethod
    def validate_position_value_quote(cls, v):
        value = Decimal(str(v))
        if value <= 0:
            raise ValueError("position_value_quote must be > 0")
        return value

    @field_validator("target_base_value_pct", mode="before")
    @classmethod
    def validate_target_base_value_pct(cls, v):
        value = Decimal(str(v))
        if value < 0 or value > 1:
            raise ValueError("target_base_value_pct must be between 0 and 1")
        return value

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
        self._stop_loss_liquidation_mode = self.config.stop_loss_liquidation_mode
        self._settled_swap_executors: Set[str] = set()

        self._state: ControllerState = ControllerState.IDLE
        self._last_rebalance_ts: float = 0.0
        self._rebalance_timestamps: Deque[float] = deque(maxlen=200)
        self._rebalance_plan: Optional[RebalancePlan] = None
        self._pending_liquidation: bool = False
        self._last_liquidation_attempt_ts: float = 0.0
        self._inventory_swap_failed: Optional[bool] = None
        self._last_inventory_swap_ts: float = 0.0
        self._stop_loss_until_ts: float = 0.0
        self._last_exit_reason: Optional[str] = None
        self._anchor_value_by_executor: Dict[str, BudgetAnchor] = {}
        self._lp_failure_blocked: bool = False
        self._lp_failure_reason: Optional[str] = None

        self._fee_rate_ewma: Optional[Decimal] = None
        self._last_fee_value: Optional[Decimal] = None
        self._last_fee_ts: Optional[float] = None
        self._last_fee_position: Optional[str] = None
        self._inventory_ratio: Optional[Decimal] = None
        self._inventory_ratio_ema: Optional[Decimal] = None
        self._inventory_deviation: Optional[Decimal] = None
        self._inventory_skew: Decimal = Decimal("0")
        self._last_cost_filter_log_ts: float = 0.0

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")

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
        now = self.market_data_provider.time()
        current_price = self._get_current_price()
        lp_executor = self._get_active_lp_executor()
        self._update_inventory_metrics(current_price, lp_executor)
        self._update_fee_rate_estimate(current_price, now, lp_executor)
        lp_state = (lp_executor.custom_info or {}).get("state") if lp_executor else None
        anchor = self._anchor_value_by_executor.get(lp_executor.id) if lp_executor else None
        self.processed_data = {
            "current_price": current_price,
            "wallet_base": self._wallet_base,
            "wallet_quote": self._wallet_quote,
            "controller_state": self._state.value,
            "lp_state": lp_state,
            "stop_loss_anchor": anchor.value_quote if anchor else None,
            "pending_liquidation": self._pending_liquidation,
            "rebalance_stage": self._rebalance_plan.stage.value if self._rebalance_plan else None,
            "inventory_swap_failed": self._inventory_swap_failed,
            "lp_failure_blocked": self._lp_failure_blocked,
        }

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions: List[ExecutorAction] = []
        now = self.market_data_provider.time()

        self._reconcile_swaps()

        lp_executor = self._get_active_lp_executor()
        swap_executor = self._get_active_swap_executor()

        if self._handle_manual_kill_switch(lp_executor, actions):
            return actions
        if self._handle_swap_in_progress(swap_executor):
            return actions
        if self._handle_lp_failure(lp_executor, actions):
            return actions
        if self._handle_active_lp(lp_executor, now, actions):
            return actions
        if self._handle_pending_liquidation(actions):
            return actions
        if self._handle_stop_loss_cooldown(now):
            return actions
        if self._handle_rebalance_plan(now, actions):
            return actions

        self._handle_entry(now, actions)
        return actions

    def _handle_manual_kill_switch(self, lp_executor: Optional[ExecutorInfo], actions: List[ExecutorAction]) -> bool:
        if not self.config.manual_kill_switch:
            return False
        self._set_state(ControllerState.MANUAL_STOP, "manual_kill_switch")
        if lp_executor:
            actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=lp_executor.id))
        return True

    def _handle_swap_in_progress(self, swap_executor: Optional[ExecutorInfo]) -> bool:
        if not swap_executor:
            return False
        swap_label = swap_executor.config.level_id or "swap"
        self._set_state(ControllerState.WAIT_SWAP, f"{swap_label}_in_progress")
        return True

    def _handle_lp_failure(self, lp_executor: Optional[ExecutorInfo], actions: List[ExecutorAction]) -> bool:
        if self._lp_failure_blocked:
            self._set_state(ControllerState.LP_FAILURE, self._lp_failure_reason or "lp_failure")
            return True
        reason = self._detect_lp_failure()
        if not reason:
            return False
        self._lp_failure_blocked = True
        self._lp_failure_reason = reason
        self._clear_rebalance_context()
        self._reset_inventory_swap_state()
        self._pending_liquidation = False
        self._set_state(ControllerState.LP_FAILURE, reason)
        self.logger().error("LP executor failure detected (%s). Manual intervention required.", reason)
        if lp_executor and lp_executor.is_active:
            actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=lp_executor.id))
        return True

    def _handle_active_lp(
        self,
        lp_executor: Optional[ExecutorInfo],
        now: float,
        actions: List[ExecutorAction],
    ) -> bool:
        if not lp_executor:
            return False
        self._set_state(ControllerState.ACTIVE)
        current_price = self._get_current_price()
        self._ensure_anchor_value(lp_executor, current_price)
        stop_action = self._maybe_stop_for_stop_loss(lp_executor, now, current_price)
        if stop_action:
            self._set_state(ControllerState.STOPLOSS_PAUSE, "stop_loss_triggered")
            actions.append(stop_action)
            return True

        stop_action = self._maybe_stop_for_rebalance(lp_executor, now)
        if stop_action:
            self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "out_of_range_rebalance")
            actions.append(stop_action)
            return True

        return True

    def _handle_pending_liquidation(self, actions: List[ExecutorAction]) -> bool:
        if not self._pending_liquidation:
            return False
        now = self.market_data_provider.time()
        if self._last_liquidation_attempt_ts > 0:
            if (now - self._last_liquidation_attempt_ts) < self.config.cooldown_seconds:
                self._set_state(ControllerState.STOPLOSS_PAUSE, "liquidation_cooldown")
                return True
        current_price = self._get_current_price()
        if current_price is None or current_price <= 0:
            self._set_state(ControllerState.STOPLOSS_PAUSE, "price_unavailable")
            return True
        snapshot = self._build_budget_snapshot(current_price, allow_partial=True)
        if snapshot is None:
            self._set_state(ControllerState.STOPLOSS_PAUSE, "liquidation_wait_balance")
            return True
        if snapshot.base_amount <= 0:
            if snapshot.quote_amount > 0:
                self._pending_liquidation = False
                self._set_state(ControllerState.STOPLOSS_PAUSE, "stop_loss_no_liquidation")
            else:
                self._set_state(ControllerState.STOPLOSS_PAUSE, "liquidation_wait_balance")
            return True

        swap_action = self._build_liquidation_action(snapshot)
        if swap_action:
            self._last_liquidation_attempt_ts = now
            self._set_state(ControllerState.WAIT_SWAP, "stop_loss_liquidation")
            actions.append(swap_action)
        else:
            self._set_state(ControllerState.STOPLOSS_PAUSE, "liquidation_wait_balance")
        return True

    def _handle_stop_loss_cooldown(self, now: float) -> bool:
        if now >= self._stop_loss_until_ts:
            return False
        self._set_state(ControllerState.STOPLOSS_PAUSE, "cooldown")
        return True

    def _handle_rebalance_plan(self, now: float, actions: List[ExecutorAction]) -> bool:
        if self._rebalance_plan is None:
            return False
        actions.extend(self._advance_rebalance_plan(now))
        return True

    def _handle_entry(self, now: float, actions: List[ExecutorAction]):
        if not self._can_start_new_position(now):
            self._set_state(ControllerState.IDLE, "entry_blocked")
            return

        current_price = self._get_current_price()
        context, reason = self._build_budget_context(current_price, allow_partial=False)
        if context is None:
            self._set_state(ControllerState.IDLE, reason or "budget_unavailable")
            return

        budget = context.budget
        delta_base = context.delta_base
        swap_required = context.swap_required

        if swap_required:
            if not self.config.auto_swap_enabled:
                self._set_state(ControllerState.IDLE, "swap_required")
                return
            if not self._is_inventory_swap_allowed():
                self._set_state(ControllerState.IDLE, "swap_blocked")
                return
            if self._last_inventory_swap_ts > 0 and (now - self._last_inventory_swap_ts) < self.config.cooldown_seconds:
                reason = "swap_retry_cooldown" if self._inventory_swap_failed else "swap_cooldown"
                self._set_state(ControllerState.IDLE, reason)
                return
            swap_action = self._build_inventory_swap_action(current_price)
            if swap_action:
                self._set_state(ControllerState.INVENTORY_SWAP, "entry_inventory")
                actions.append(swap_action)
                return
            self._set_state(ControllerState.IDLE, "swap_required")
            return

        self._set_state(ControllerState.READY_TO_OPEN, "entry_open")
        lp_action = self._create_lp_executor_action(budget.target_base, budget.target_quote)
        if lp_action:
            actions.append(lp_action)
            self._set_state(ControllerState.ACTIVE, "lp_open")
        else:
            self._set_state(ControllerState.IDLE, "lp_open_failed")

    def _get_active_lp_executor(self) -> Optional[ExecutorInfo]:
        active = [
            e for e in self.executors_info
            if e.is_active and e.type == "lp_position_executor" and e.controller_id == self.config.id
        ]
        return active[0] if active else None

    def _get_active_swap_executor(self) -> Optional[ExecutorInfo]:
        active = [
            e for e in self.executors_info
            if e.is_active and e.type == "gateway_swap_executor" and e.controller_id == self.config.id
        ]
        return active[0] if active else None

    def _detect_lp_failure(self) -> Optional[str]:
        for executor in self.executors_info:
            if executor.type != "lp_position_executor":
                continue
            if executor.controller_id != self.config.id:
                continue
            state = (executor.custom_info or {}).get("state")
            if state == LPPositionStates.RETRIES_EXCEEDED.value:
                return "retries_exceeded"
            if executor.close_type == CloseType.FAILED:
                return "executor_failed"
        return None

    def _get_current_price(self) -> Optional[Decimal]:
        price = self.market_data_provider.get_rate(self.config.trading_pair)
        if price is None:
            return None
        try:
            return Decimal(str(price))
        except Exception:
            return None

    async def _update_wallet_balances(self):
        balances_base: List[Decimal] = []
        balances_quote: List[Decimal] = []
        connector_names = {self.config.connector_name, self.config.router_connector}
        for connector_name in connector_names:
            connector = self.market_data_provider.connectors.get(connector_name)
            if connector is None:
                continue
            try:
                await connector.update_balances(on_interval=False)
                balances_base.append(Decimal(str(connector.get_balance(self._base_token) or 0)))
                balances_quote.append(Decimal(str(connector.get_balance(self._quote_token) or 0)))
            except Exception:
                continue
        if balances_base:
            # Use the freshest non-zero values across connectors sharing the same wallet.
            self._wallet_base = max(balances_base)
        if balances_quote:
            self._wallet_quote = max(balances_quote)

    def _update_fee_rate_estimate(
        self,
        current_price: Optional[Decimal],
        now: float,
        executor: Optional[ExecutorInfo],
    ):
        if current_price is None or current_price <= 0 or executor is None:
            return
        custom = executor.custom_info or {}
        if custom.get("state") != LPPositionStates.IN_RANGE.value:
            return
        position_address = custom.get("position_address")
        if position_address and self._last_fee_position != position_address:
            self._last_fee_position = position_address
            self._last_fee_value = None
            self._last_fee_ts = None
            self._fee_rate_ewma = None
            return

        base_fee = Decimal(str(custom.get("base_fee", 0)))
        quote_fee = Decimal(str(custom.get("quote_fee", 0)))
        pending_fee = base_fee * current_price + quote_fee

        if self._last_fee_ts is None or self._last_fee_value is None:
            self._last_fee_ts = now
            self._last_fee_value = pending_fee
            return

        dt = Decimal(str(now - self._last_fee_ts))
        if dt <= 0:
            return
        if dt < COST_FILTER_FEE_SAMPLE_MIN_SECONDS:
            return

        delta = pending_fee - self._last_fee_value
        if delta < 0:
            self._last_fee_ts = now
            self._last_fee_value = pending_fee
            return

        fee_rate = delta / dt
        alpha = COST_FILTER_FEE_EWMA_ALPHA
        if self._fee_rate_ewma is None:
            self._fee_rate_ewma = fee_rate
        else:
            self._fee_rate_ewma = (self._fee_rate_ewma * (Decimal("1") - alpha)) + (fee_rate * alpha)

        self._last_fee_ts = now
        self._last_fee_value = pending_fee

    def _normalize_ratio_value(self, value: Decimal) -> Decimal:
        ratio = Decimal(str(value))
        if ratio > 1:
            ratio = ratio / Decimal("100")
        if ratio < 0:
            return Decimal("0")
        if ratio > 1:
            return Decimal("1")
        return ratio

    def _wallet_total_value(self, current_price: Optional[Decimal]) -> Optional[Decimal]:
        if current_price is None or current_price <= 0:
            return None
        return self._wallet_base * current_price + self._wallet_quote

    def _portfolio_total_value(
        self,
        current_price: Optional[Decimal],
        executor: Optional[ExecutorInfo],
    ) -> Optional[Decimal]:
        if current_price is None or current_price <= 0:
            return None
        deployed_base, deployed_quote = self._get_deployed_amounts(executor)
        return (self._wallet_base + deployed_base) * current_price + (self._wallet_quote + deployed_quote)

    def _build_position_budget(self, current_price: Optional[Decimal]) -> Optional[PositionBudget]:
        if current_price is None or current_price <= 0:
            return None
        total_value = max(Decimal("0"), self.config.position_value_quote)
        if total_value <= 0:
            return None
        ratio = self._normalized_target_ratio()
        base_value = total_value * ratio
        quote_value = total_value - base_value
        base_amount = base_value / current_price
        return PositionBudget(
            total_value_quote=total_value,
            target_base=base_amount,
            target_quote=quote_value,
        )

    def _build_budget_snapshot(
        self,
        current_price: Optional[Decimal],
        allow_partial: bool,
    ) -> Optional[BudgetSnapshot]:
        if current_price is None or current_price <= 0:
            return None
        total_value = self._wallet_total_value(current_price)
        if total_value is None or total_value <= 0:
            return None
        target_value = max(Decimal("0"), self.config.position_value_quote)
        if target_value <= 0:
            return None
        if total_value < target_value and not allow_partial:
            return None
        scale = min(Decimal("1"), target_value / total_value)
        base_amount = self._wallet_base * scale
        quote_amount = self._wallet_quote * scale
        total_value = base_amount * current_price + quote_amount
        return BudgetSnapshot(
            total_value_quote=total_value,
            base_amount=base_amount,
            quote_amount=quote_amount,
            scale=scale,
        )

    def _build_budget_context(
        self,
        current_price: Optional[Decimal],
        allow_partial: bool,
    ) -> Tuple[Optional[BudgetContext], Optional[str]]:
        if current_price is None or current_price <= 0:
            return None, "price_unavailable"
        snapshot = self._build_budget_snapshot(current_price, allow_partial=allow_partial)
        if snapshot is None:
            return None, "insufficient_balance"
        budget = self._build_position_budget(current_price)
        if budget is None:
            return None, "budget_unavailable"
        delta_base = budget.target_base - snapshot.base_amount
        delta_quote_value = abs(delta_base * current_price)
        swap_required = delta_quote_value >= self.config.swap_min_quote_value
        return BudgetContext(
            budget=budget,
            snapshot=snapshot,
            delta_base=delta_base,
            delta_quote_value=delta_quote_value,
            swap_required=swap_required,
        ), None

    def _has_sufficient_wallet(self, current_price: Optional[Decimal]) -> bool:
        return self._build_budget_snapshot(current_price, allow_partial=False) is not None

    def _update_inventory_metrics(self, current_price: Optional[Decimal], executor: Optional[ExecutorInfo]):
        if not self.config.inventory_skew_enabled:
            self._inventory_ratio = None
            self._inventory_ratio_ema = None
            self._inventory_deviation = None
            self._inventory_skew = Decimal("0")
            return
        if current_price is None or current_price <= 0:
            return

        deployed_base, deployed_quote = self._get_deployed_amounts(executor)
        base_total = self._wallet_base + deployed_base
        quote_total = self._wallet_quote + deployed_quote
        total_value = base_total * current_price + quote_total
        if total_value <= 0:
            return

        ratio = (base_total * current_price) / total_value
        self._inventory_ratio = ratio

        alpha = self.config.inventory_skew_ema_alpha
        if self._inventory_ratio_ema is None:
            self._inventory_ratio_ema = ratio
        else:
            self._inventory_ratio_ema = (self._inventory_ratio_ema * (Decimal("1") - alpha)) + (ratio * alpha)

        target_ratio = self._normalized_target_ratio()
        deviation = self._inventory_ratio_ema - target_ratio
        self._inventory_deviation = deviation

        skew_raw = deviation * self.config.inventory_skew_k
        skew = max(-self.config.inventory_skew_max, min(self.config.inventory_skew_max, skew_raw))
        if abs(skew - self._inventory_skew) >= self.config.inventory_skew_step_min:
            self._inventory_skew = skew

    def _get_deployed_amounts(self, executor: Optional[ExecutorInfo]) -> Tuple[Decimal, Decimal]:
        if executor is None:
            return Decimal("0"), Decimal("0")
        custom = executor.custom_info or {}
        if custom.get("state") in [LPPositionStates.OPENING.value, LPPositionStates.CLOSING.value]:
            return Decimal("0"), Decimal("0")
        base_amount = Decimal(str(custom.get("base_amount", 0)))
        quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        base_fee = Decimal(str(custom.get("base_fee", 0)))
        quote_fee = Decimal(str(custom.get("quote_fee", 0)))
        return base_amount + base_fee, quote_amount + quote_fee

    def _get_planned_widths(self) -> Tuple[Decimal, Decimal]:
        total_width = self.config.position_width_pct / Decimal("100")
        half_width = total_width / Decimal("2")
        if not self.config.inventory_skew_enabled:
            return half_width, half_width
        min_width = self.config.inventory_skew_min_width_pct / Decimal("100")
        return apply_inventory_skew_to_widths(
            total_width=total_width,
            skew=self._inventory_skew,
            min_width=min_width,
        )

    def _maybe_log_cost_filter(self, allowed: bool, details: Dict, now: float):
        interval = max(self.config.cooldown_seconds, 60)
        if (now - self._last_cost_filter_log_ts) < interval:
            return
        self._last_cost_filter_log_ts = now
        reason = details.get("reason", "unknown")
        self.logger().info(
            "Cost filter %s: reason=%s fee_rate=%.8f(%s) in_range=%.2f(%s) "
            "widths=%.4f/%.4f expected=%.6f cost=%.6f fixed=%.6f swap_notional=%.6f "
            "swap_cost=%.6f payback=%.2f",
            "ALLOW" if allowed else "BLOCK",
            reason,
            float(details.get("fee_rate", Decimal("0"))),
            details.get("fee_rate_source", "n/a"),
            float(details.get("in_range_time", Decimal("0"))),
            details.get("in_range_source", "n/a"),
            float(details.get("lower_width", Decimal("0"))),
            float(details.get("upper_width", Decimal("0"))),
            float(details.get("expected_fees", Decimal("0"))),
            float(details.get("cost", Decimal("0"))),
            float(details.get("fixed_cost", Decimal("0"))),
            float(details.get("swap_notional", Decimal("0"))),
            float(details.get("swap_cost", Decimal("0"))),
            float(details.get("payback_sec", Decimal("0"))),
        )

    def _estimate_position_value(self, executor: ExecutorInfo, current_price: Decimal) -> Decimal:
        custom = executor.custom_info or {}
        base_amount = Decimal(str(custom.get("base_amount", 0)))
        quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        base_fee = Decimal(str(custom.get("base_fee", 0)))
        quote_fee = Decimal(str(custom.get("quote_fee", 0)))
        return (base_amount + base_fee) * current_price + (quote_amount + quote_fee)

    def _build_budget_anchor(
        self,
        current_price: Optional[Decimal],
        executor: Optional[ExecutorInfo],
    ) -> Optional[BudgetAnchor]:
        if current_price is None or current_price <= 0 or executor is None:
            return None
        budget_value = max(Decimal("0"), self.config.position_value_quote)
        if budget_value <= 0:
            return None
        deployed_value = self._estimate_position_value(executor, current_price)
        wallet_value = self._wallet_total_value(current_price) or Decimal("0")
        remaining_value = budget_value - deployed_value
        if remaining_value <= 0 or wallet_value <= 0:
            anchor_value = max(Decimal("0"), deployed_value)
            if anchor_value <= 0:
                return None
            return BudgetAnchor(
                value_quote=anchor_value,
                wallet_base_amount=Decimal("0"),
                wallet_quote_amount=Decimal("0"),
            )

        budget_wallet_value = min(wallet_value, remaining_value)
        base_value = self._wallet_base * current_price
        base_ratio = base_value / wallet_value if wallet_value > 0 else Decimal("0")
        base_slice_value = budget_wallet_value * base_ratio
        wallet_base_amount = base_slice_value / current_price
        wallet_quote_amount = budget_wallet_value - base_slice_value
        anchor_value = deployed_value + budget_wallet_value
        if anchor_value <= 0:
            return None
        return BudgetAnchor(
            value_quote=anchor_value,
            wallet_base_amount=wallet_base_amount,
            wallet_quote_amount=wallet_quote_amount,
        )

    def _calculate_total_equity(
        self,
        current_price: Optional[Decimal],
        executor: Optional[ExecutorInfo],
    ) -> Optional[Decimal]:
        if current_price is None or current_price <= 0 or executor is None:
            return None
        deployed_value = self._estimate_position_value(executor, current_price)
        anchor = self._anchor_value_by_executor.get(executor.id)
        wallet_slice_value = Decimal("0")
        if anchor is not None:
            wallet_slice_value = anchor.wallet_base_amount * current_price + anchor.wallet_quote_amount
        equity = deployed_value + wallet_slice_value
        return equity if equity > 0 else None

    def _ensure_anchor_value(self, executor: ExecutorInfo, current_price: Optional[Decimal]):
        anchor_value = self._anchor_value_by_executor.get(executor.id)
        if anchor_value is not None and anchor_value.value_quote > 0:
            return
        anchor = self._build_budget_anchor(current_price, executor)
        if anchor is None:
            return
        self._anchor_value_by_executor[executor.id] = anchor
        self.logger().info("Anchor budget initialized: %.6f", float(anchor.value_quote))

    def _is_inventory_swap_allowed(self) -> bool:
        if not self.config.inventory_skew_enabled:
            return True
        if self._inventory_deviation is None:
            return False
        deviation = abs(self._inventory_deviation)
        soft_band = self._normalize_ratio_value(self.config.inventory_soft_band_pct)
        hard_band = self._normalize_ratio_value(self.config.inventory_hard_band_pct)
        if hard_band > 0:
            hard_band = max(hard_band, soft_band)
            if deviation < soft_band:
                return False
            return deviation >= hard_band
        if soft_band <= 0:
            return True
        return deviation >= soft_band

    def _can_start_new_position(self, now: float) -> bool:
        if not self._is_entry_triggered():
            return False
        if not self.config.reenter_enabled and self._last_exit_reason == "stop_loss":
            return False
        if now < self._stop_loss_until_ts:
            return False
        if self._rebalance_plan is not None:
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

    def _maybe_stop_for_stop_loss(
        self,
        executor: ExecutorInfo,
        now: float,
        current_price: Optional[Decimal],
    ) -> Optional[StopExecutorAction]:
        if self.config.stop_loss_pnl_pct <= 0:
            return None
        anchor_value = self._anchor_value_by_executor.get(executor.id)
        if anchor_value is None or anchor_value.value_quote <= 0:
            return None
        equity = self._calculate_total_equity(current_price, executor)
        if equity is None:
            return None
        trigger_level = anchor_value.value_quote - (anchor_value.value_quote * self.config.stop_loss_pnl_pct)
        if equity <= trigger_level:
            self._last_exit_reason = "stop_loss"
            self._stop_loss_until_ts = now + self.config.stop_loss_pause_sec
            if self._stop_loss_liquidation_mode == StopLossLiquidationMode.QUOTE:
                self._pending_liquidation = True
                self._last_liquidation_attempt_ts = 0.0
            self._reset_inventory_swap_state()
            self._clear_rebalance_context()
            return StopExecutorAction(controller_id=self.config.id, executor_id=executor.id)
        return None

    def _maybe_stop_for_rebalance(self, executor: ExecutorInfo, now: float) -> Optional[StopExecutorAction]:
        if self._rebalance_plan is not None:
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
        allow_rebalance, cost_details = evaluate_cost_filter(
            enabled=self.config.cost_filter_enabled,
            current_price=current_price,
            position_value=self._estimate_position_value(executor, current_price),
            fee_rate_ewma=self._fee_rate_ewma,
            fee_rate_bootstrap_quote_per_hour=self.config.cost_filter_fee_rate_bootstrap_quote_per_hour,
            position_width_pct=self.config.position_width_pct,
            auto_swap_enabled=self.config.auto_swap_enabled,
            swap_slippage_pct=self.config.swap_slippage_pct,
            fixed_cost_quote=self.config.cost_filter_fixed_cost_quote,
            max_payback_sec=self.config.cost_filter_max_payback_sec,
        )
        if not allow_rebalance and should_force_rebalance(
            now,
            out_of_range_since,
            self.config.rebalance_seconds,
        ):
            cost_details["reason"] = "force_rebalance"
            allow_rebalance = True
        if self.config.cost_filter_enabled:
            self._maybe_log_cost_filter(allow_rebalance, cost_details, now)
        if not allow_rebalance:
            return None

        self._rebalance_plan = RebalancePlan(
            info=custom.copy(),
            reopen_after_ts=now + self.config.reopen_delay_sec,
            stage=RebalanceStage.WAIT_REOPEN,
            swap_attempted=False,
            swap_failed=None,
        )
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

    def _build_liquidation_action(self, snapshot: BudgetSnapshot) -> Optional[CreateExecutorAction]:
        base_amount = snapshot.base_amount
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
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _build_inventory_swap_action(self, current_price: Decimal) -> Optional[CreateExecutorAction]:
        executor_config = self._calculate_inventory_swap_config(current_price)
        if executor_config is None:
            return None
        self._inventory_swap_failed = None
        self._last_inventory_swap_ts = self.market_data_provider.time()
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _build_rebalance_swap_action(self, current_price: Decimal) -> Optional[CreateExecutorAction]:
        plan = self._rebalance_plan
        if plan is None:
            return None
        executor_config = self._calculate_inventory_swap_config(current_price)
        if executor_config is None:
            return None
        plan.swap_attempted = True
        plan.swap_failed = None
        plan.swap_last_ts = self.market_data_provider.time()
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _calculate_inventory_swap_config(self, current_price: Decimal) -> Optional[GatewaySwapExecutorConfig]:
        if current_price is None or current_price <= 0:
            return None
        if not self._is_inventory_swap_allowed():
            return None
        context, _ = self._build_budget_context(current_price, allow_partial=False)
        if context is None:
            return None

        delta_base = context.delta_base
        delta_quote_value = context.delta_quote_value
        if delta_quote_value < self.config.swap_min_quote_value:
            return None

        if delta_base > 0:
            quote_amount = self._apply_swap_buffer(delta_quote_value)
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

        base_amount = self._apply_swap_buffer(abs(delta_base))
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
        return Decimal(str(self.config.target_base_value_pct))

    def _create_lp_executor_action(self, base_amt: Decimal, quote_amt: Decimal) -> Optional[CreateExecutorAction]:
        executor_config = self._create_lp_executor_config(base_amt=base_amt, quote_amt=quote_amt)
        if executor_config is None:
            return None
        self._clear_rebalance_context()
        self._reset_inventory_swap_state()
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _create_lp_executor_config(
        self,
        base_amt: Decimal,
        quote_amt: Decimal,
    ) -> Optional[LPPositionExecutorConfig]:
        current_price = self._get_current_price()
        if current_price is None or current_price <= 0:
            return None

        if base_amt <= 0 and quote_amt <= 0:
            return None

        lower_price, upper_price = self._calculate_price_bounds(current_price, base_amt, quote_amt)
        if self.config.inventory_skew_enabled and base_amt > 0 and quote_amt > 0:
            lower_width, upper_width = self._get_planned_widths()
            ratio_used = self._normalized_target_ratio()
            ratio_source = "target"
            self.logger().info(
                "Inventory skew applied: skew=%.4f ratio=%.4f target=%.4f deviation=%.4f "
                "widths=%.4f/%.4f ratio_used=%.4f(%s)",
                float(self._inventory_skew),
                float(self._inventory_ratio_ema or Decimal("0")),
                float(self._normalized_target_ratio()),
                float(self._inventory_deviation or Decimal("0")),
                float(lower_width),
                float(upper_width),
                float(ratio_used),
                ratio_source,
            )

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
        reservation_id = self._reserve_budget(base_amt, quote_amt)
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
            lower_width, upper_width = self._get_planned_widths()
            lower_price = current_price * (Decimal("1") - lower_width)
            upper_price = current_price * (Decimal("1") + upper_width)
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

    def _reserve_budget(self, base_amt: Decimal, quote_amt: Decimal) -> Optional[str]:
        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is None:
            self.logger().warning(
                "Budget reserve failed: connector unavailable (base=%.6f quote=%.6f)",
                float(base_amt),
                float(quote_amt),
            )
            return None
        requirements = {}
        if base_amt > 0:
            requirements[self._base_token] = base_amt
        if quote_amt > 0:
            requirements[self._quote_token] = quote_amt
        reservation_id = self._budget_coordinator.reserve(
            connector_name=self.config.connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self.config.native_token_symbol,
            min_native_balance=self.config.min_native_balance,
        )
        if reservation_id is None:
            self.logger().warning(
                "Budget reserve failed: insufficient balance (base=%.6f quote=%.6f)",
                float(base_amt),
                float(quote_amt),
            )
        return reservation_id

    def _set_state(self, state: ControllerState, reason: Optional[str] = None):
        if state == self._state:
            return
        previous = self._state
        self._state = state
        message = f"Controller state change: {previous.value} -> {state.value}"
        if reason:
            message = f"{message} ({reason})"
        self.logger().info(message)

    def _advance_rebalance_plan(self, now: float) -> List[ExecutorAction]:
        actions: List[ExecutorAction] = []
        plan = self._rebalance_plan
        if plan is None:
            return actions

        current_price = self._get_current_price()
        context, reason = self._build_budget_context(current_price, allow_partial=False)
        if context is None:
            self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, reason or "budget_unavailable")
            return actions

        budget = context.budget
        delta_base = context.delta_base
        swap_required = context.swap_required

        if plan.stage == RebalanceStage.WAIT_REOPEN:
            if now < plan.reopen_after_ts:
                self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "reopen_delay")
                return actions
            plan.stage = RebalanceStage.SWAP_PENDING

        if plan.stage == RebalanceStage.SWAP_PENDING:
            if swap_required:
                if not self.config.auto_swap_enabled:
                    self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "swap_required")
                    return actions
                if not self._is_inventory_swap_allowed():
                    self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "swap_blocked")
                    return actions
                if plan.swap_failed:
                    if (now - plan.swap_last_ts) < self.config.cooldown_seconds:
                        self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "swap_retry_cooldown")
                        return actions
                    plan.swap_failed = None
                    plan.swap_attempted = False
                if not plan.swap_attempted:
                    swap_action = self._build_rebalance_swap_action(current_price)
                    if swap_action:
                        self._set_state(ControllerState.INVENTORY_SWAP, "rebalance_inventory")
                        actions.append(swap_action)
                        return actions
                    self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "swap_required")
                    return actions
                if plan.swap_attempted and plan.swap_failed is False:
                    plan.stage = RebalanceStage.READY_TO_OPEN
                else:
                    self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "swap_pending")
                    return actions
            else:
                plan.stage = RebalanceStage.READY_TO_OPEN

        if plan.stage == RebalanceStage.READY_TO_OPEN:
            if swap_required:
                plan.stage = RebalanceStage.SWAP_PENDING
                return actions
            self._set_state(ControllerState.READY_TO_OPEN, "rebalance_open")
            lp_action = self._create_lp_executor_action(budget.target_base, budget.target_quote)
            if lp_action:
                actions.append(lp_action)
                self._set_state(ControllerState.ACTIVE, "lp_open")
            else:
                self._set_state(ControllerState.IDLE, "lp_open_failed")
            return actions

        return actions

    def _clear_rebalance_context(self):
        self._rebalance_plan = None

    def _reset_inventory_swap_state(self):
        self._inventory_swap_failed = None
        self._last_inventory_swap_ts = 0.0

    def _reconcile_swaps(self):
        now = self.market_data_provider.time()
        for executor in self.executors_info:
            if executor.type != "gateway_swap_executor":
                continue
            if executor.controller_id != self.config.id:
                continue
            if not executor.is_done:
                continue
            if executor.id in self._settled_swap_executors:
                continue
            self._settled_swap_executors.add(executor.id)
            if executor.config.level_id == "auto_swap":
                swap_failed = executor.close_type != CloseType.COMPLETED
                if self._rebalance_plan and self._rebalance_plan.stage in {
                    RebalanceStage.SWAP_PENDING,
                    RebalanceStage.READY_TO_OPEN,
                }:
                    self._rebalance_plan.swap_attempted = True
                    self._rebalance_plan.swap_failed = swap_failed
                else:
                    self._inventory_swap_failed = swap_failed
                    self._last_inventory_swap_ts = now
            elif executor.config.level_id == "liquidate":
                self._last_liquidation_attempt_ts = now
                if executor.close_type == CloseType.COMPLETED:
                    self._pending_liquidation = False
                else:
                    self._pending_liquidation = True
