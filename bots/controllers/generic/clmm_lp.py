import logging
from collections import deque
from dataclasses import dataclass
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


def required_base_ratio_from_range(
    *,
    price: Decimal,
    lower_price: Decimal,
    upper_price: Decimal,
) -> Optional[Decimal]:
    if price <= 0 or lower_price <= 0 or upper_price <= 0:
        return None
    if lower_price >= upper_price:
        return None
    if price <= lower_price:
        return Decimal("1")
    if price >= upper_price:
        return Decimal("0")

    sp = price.sqrt()
    sl = lower_price.sqrt()
    su = upper_price.sqrt()
    if sp <= 0 or su <= 0:
        return None

    amount_base = (su - sp) / (sp * su)
    amount_quote = sp - sl
    if amount_base <= 0 and amount_quote <= 0:
        return None
    if amount_base <= 0:
        return Decimal("0")

    base_value = amount_base * price
    total_value = base_value + amount_quote
    if total_value <= 0:
        return None
    ratio = base_value / total_value
    if ratio < 0:
        return Decimal("0")
    if ratio > 1:
        return Decimal("1")
    return ratio


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
        self._rebalance_plan: Optional[RebalancePlan] = None
        self._pending_liquidation: bool = False
        self._inventory_swap_attempted: bool = False
        self._inventory_swap_failed: Optional[bool] = None
        self._stop_loss_until_ts: float = 0.0
        self._last_exit_reason: Optional[str] = None
        self._anchor_value_by_executor: Dict[str, Decimal] = {}

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
        now = self.market_data_provider.time()
        current_price = self._get_current_price()
        lp_executor = self._get_active_lp_executor()
        self._update_inventory_metrics(current_price, lp_executor)
        self._update_fee_rate_estimate(current_price, now, lp_executor)
        budget_snapshot = self._budget_pool.snapshot() if self._budget_pool else None
        self.processed_data = {
            "current_price": current_price,
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

        if self._handle_manual_kill_switch(lp_executor, actions):
            return actions
        if self._handle_swap_in_progress(swap_executor):
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

    def _handle_active_lp(
        self,
        lp_executor: Optional[ExecutorInfo],
        now: float,
        actions: List[ExecutorAction],
    ) -> bool:
        if not lp_executor:
            return False
        self._set_state(ControllerState.ACTIVE)
        stop_action = self._maybe_stop_for_stop_loss(lp_executor, now)
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
        swap_action = self._build_liquidation_action()
        if swap_action:
            self._pending_liquidation = False
            self._set_state(ControllerState.WAIT_SWAP, "stop_loss_liquidation")
            actions.append(swap_action)
        else:
            self._pending_liquidation = False
            self._set_state(ControllerState.STOPLOSS_PAUSE, "stop_loss_no_liquidation")
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

        if self.config.auto_swap_enabled and not self._inventory_swap_attempted:
            swap_action = self._build_inventory_swap_action()
            if swap_action:
                self._set_state(ControllerState.INVENTORY_SWAP, "entry_inventory")
                actions.append(swap_action)
                return

        self._set_state(ControllerState.READY_TO_OPEN, "entry_open")
        lp_action = self._create_lp_executor_action(use_single_sided=False)
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

    def _update_inventory_metrics(self, current_price: Optional[Decimal], executor: Optional[ExecutorInfo]):
        if not self.config.inventory_skew_enabled:
            self._inventory_ratio = None
            self._inventory_ratio_ema = None
            self._inventory_deviation = None
            self._inventory_skew = Decimal("0")
            return
        if current_price is None or current_price <= 0:
            return

        base_available, quote_available = self._get_budget_balances()
        deployed_base, deployed_quote = self._get_deployed_amounts(executor)
        base_total = base_available + deployed_base
        quote_total = quote_available + deployed_quote
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

    def _build_rebalance_swap_action(self) -> Optional[CreateExecutorAction]:
        plan = self._rebalance_plan
        if plan is None:
            return None
        executor_config = self._calculate_inventory_swap_config()
        if executor_config is None:
            return None
        self._record_swap_adjustment(executor_config)
        plan.swap_attempted = True
        plan.swap_failed = None
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _calculate_inventory_swap_config(self) -> Optional[GatewaySwapExecutorConfig]:
        price = self._get_current_price()
        if price is None or price <= 0:
            return None
        if not self._is_inventory_swap_allowed():
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
        return self._normalize_ratio_value(self.config.target_base_value_pct)

    def _calculate_target_allocation_amounts(
        self,
        price: Decimal,
        ratio_override: Optional[Decimal] = None,
    ) -> Tuple[Decimal, Decimal]:
        base_available, quote_available = self._get_budget_balances()
        ratio_value = ratio_override if ratio_override is not None else self._normalized_target_ratio()
        ratio = self._normalize_ratio_value(ratio_value)
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

    def _should_open_single_sided(self, plan: RebalancePlan) -> bool:
        if not self.config.auto_swap_enabled:
            return True
        return plan.swap_failed is True

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

        ratio_override = None
        if self._rebalance_plan and use_single_sided:
            info = self._rebalance_plan.info
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
                if self.config.inventory_skew_enabled:
                    lower_width, upper_width = self._get_planned_widths()
                    lower_price = current_price * (Decimal("1") - lower_width)
                    upper_price = current_price * (Decimal("1") + upper_width)
                    ratio_override = required_base_ratio_from_range(
                        price=current_price,
                        lower_price=lower_price,
                        upper_price=upper_price,
                    )
                    if ratio_override is not None:
                        ratio_override = self._normalize_ratio_value(ratio_override)
                        if ratio_override <= 0 or ratio_override >= 1:
                            ratio_override = None
                base_amt, quote_amt = self._calculate_target_allocation_amounts(
                    current_price,
                    ratio_override=ratio_override,
                )
            else:
                base_amt = self.config.base_amount
                quote_amt = self.config.quote_amount

        if base_amt <= 0 and quote_amt <= 0:
            return None

        lower_price, upper_price = self._calculate_price_bounds(current_price, base_amt, quote_amt)
        if self.config.inventory_skew_enabled and base_amt > 0 and quote_amt > 0:
            lower_width, upper_width = self._get_planned_widths()
            ratio_used = ratio_override if ratio_override is not None else self._normalized_target_ratio()
            ratio_source = "range" if ratio_override is not None else "target"
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

    def _advance_rebalance_plan(self, now: float) -> List[ExecutorAction]:
        actions: List[ExecutorAction] = []
        plan = self._rebalance_plan
        if plan is None:
            return actions

        if plan.stage == RebalanceStage.WAIT_REOPEN:
            if now < plan.reopen_after_ts:
                self._set_state(ControllerState.REBALANCE_WAIT_CLOSE, "reopen_delay")
                return actions
            plan.stage = RebalanceStage.SWAP_PENDING

        if plan.stage == RebalanceStage.SWAP_PENDING:
            if self.config.auto_swap_enabled and not plan.swap_attempted and not self._should_open_single_sided(plan):
                swap_action = self._build_rebalance_swap_action()
                if swap_action:
                    self._set_state(ControllerState.INVENTORY_SWAP, "rebalance_inventory")
                    actions.append(swap_action)
                    return actions
            plan.stage = RebalanceStage.READY_TO_OPEN

        if plan.stage == RebalanceStage.READY_TO_OPEN:
            use_single_sided = self._should_open_single_sided(plan)
            self._set_state(ControllerState.READY_TO_OPEN, "rebalance_open")
            lp_action = self._create_lp_executor_action(use_single_sided=use_single_sided)
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
            if self._rebalance_plan and self._rebalance_plan.stage in {
                RebalanceStage.SWAP_PENDING,
                RebalanceStage.READY_TO_OPEN,
            }:
                self._rebalance_plan.swap_attempted = True
                self._rebalance_plan.swap_failed = executor.close_type == CloseType.FAILED
            else:
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
