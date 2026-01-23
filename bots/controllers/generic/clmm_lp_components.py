from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, List, Optional, Set, Tuple

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


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


class IntentFlow(str, Enum):
    NONE = "NONE"
    ENTRY = "ENTRY"
    REBALANCE = "REBALANCE"
    STOPLOSS = "STOPLOSS"
    MANUAL = "MANUAL"
    FAILURE = "FAILURE"


class IntentStage(str, Enum):
    NONE = "NONE"
    WAIT = "WAIT"
    SUBMIT_SWAP = "SUBMIT_SWAP"
    SUBMIT_LP = "SUBMIT_LP"
    STOP_LP = "STOP_LP"


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


def to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def to_optional_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


@dataclass(frozen=True)
class TokenOrderMapper:
    trading_pair: str
    pool_trading_pair: str
    base_token: str
    quote_token: str
    pool_base_token: str
    pool_quote_token: str
    pool_order_inverted: bool

    @classmethod
    def from_config(cls, trading_pair: str, pool_trading_pair: Optional[str]) -> "TokenOrderMapper":
        ref_tokens = trading_pair.split("-")
        pool_pair = pool_trading_pair or trading_pair
        pool_tokens = pool_pair.split("-")
        base = ref_tokens[0] if len(ref_tokens) >= 2 else ""
        quote = ref_tokens[1] if len(ref_tokens) >= 2 else ""
        pool_base = pool_tokens[0] if len(pool_tokens) >= 2 else ""
        pool_quote = pool_tokens[1] if len(pool_tokens) >= 2 else ""
        inverted = pool_base == quote and pool_quote == base
        return cls(
            trading_pair=trading_pair,
            pool_trading_pair=pool_pair,
            base_token=base,
            quote_token=quote,
            pool_base_token=pool_base,
            pool_quote_token=pool_quote,
            pool_order_inverted=inverted,
        )

    def executor_token_order_inverted(self, executor: ExecutorInfo) -> Optional[bool]:
        config = getattr(executor, "config", None)
        base_token = getattr(config, "base_token", None)
        quote_token = getattr(config, "quote_token", None)
        if isinstance(base_token, str) and isinstance(quote_token, str):
            if base_token == self.base_token and quote_token == self.quote_token:
                return False
            if base_token == self.quote_token and quote_token == self.base_token:
                return True

        trading_pair = getattr(config, "trading_pair", None)
        if isinstance(trading_pair, str):
            parts = trading_pair.split("-")
            if len(parts) == 2:
                if parts[0] == self.base_token and parts[1] == self.quote_token:
                    return False
                if parts[0] == self.quote_token and parts[1] == self.base_token:
                    return True
        return None

    @staticmethod
    def lp_amounts_to_strategy(lp_base: Decimal, lp_quote: Decimal, inverted: bool) -> Tuple[Decimal, Decimal]:
        return (lp_quote, lp_base) if inverted else (lp_base, lp_quote)

    def strategy_amounts_to_lp(self, base_amt: Decimal, quote_amt: Decimal) -> Tuple[Decimal, Decimal]:
        return (quote_amt, base_amt) if self.pool_order_inverted else (base_amt, quote_amt)

    @staticmethod
    def lp_price_to_strategy(price: Decimal, inverted: bool) -> Decimal:
        if not inverted:
            return price
        if price <= 0:
            return price
        return Decimal("1") / price

    def strategy_price_to_lp(self, price: Decimal) -> Decimal:
        if not self.pool_order_inverted:
            return price
        if price <= 0:
            return price
        return Decimal("1") / price

    @staticmethod
    def lp_bounds_to_strategy(lower: Decimal, upper: Decimal, inverted: bool) -> Tuple[Decimal, Decimal]:
        if not inverted:
            return lower, upper
        if lower <= 0 or upper <= 0:
            return lower, upper
        mapped_lower = Decimal("1") / upper
        mapped_upper = Decimal("1") / lower
        if mapped_lower > mapped_upper:
            mapped_lower, mapped_upper = mapped_upper, mapped_lower
        return mapped_lower, mapped_upper

    def strategy_bounds_to_lp(self, lower: Decimal, upper: Decimal) -> Tuple[Decimal, Decimal]:
        if not self.pool_order_inverted:
            return lower, upper
        if lower <= 0 or upper <= 0:
            return lower, upper
        mapped_lower = Decimal("1") / upper
        mapped_upper = Decimal("1") / lower
        if mapped_lower > mapped_upper:
            mapped_lower, mapped_upper = mapped_upper, mapped_lower
        return mapped_lower, mapped_upper


@dataclass(frozen=True)
class LPView:
    executor_id: str
    is_active: bool
    is_done: bool
    close_type: Optional[CloseType]
    state: Optional[str]
    position_address: Optional[str]
    side: Optional[str]
    base_amount: Decimal
    quote_amount: Decimal
    base_fee: Decimal
    quote_fee: Decimal
    lower_price: Optional[Decimal]
    upper_price: Optional[Decimal]
    current_price: Optional[Decimal]
    out_of_range_since: Optional[float]

    @property
    def in_transition(self) -> bool:
        return self.state in {LPPositionStates.OPENING.value, LPPositionStates.CLOSING.value}


@dataclass(frozen=True)
class SwapView:
    executor_id: str
    is_active: bool
    is_done: bool
    close_type: Optional[CloseType]
    level_id: Optional[str]


@dataclass(frozen=True)
class Snapshot:
    now: float
    current_price: Optional[Decimal]
    pool_price: Optional[Decimal]
    router_price: Optional[Decimal]
    wallet_base: Decimal
    wallet_quote: Decimal
    lp: Dict[str, LPView]
    swaps: Dict[str, SwapView]

    @property
    def active_lp(self) -> List[LPView]:
        return [v for v in self.lp.values() if v.is_active]

    @property
    def active_swaps(self) -> List[SwapView]:
        return [v for v in self.swaps.values() if v.is_active]


@dataclass(frozen=True)
class PositionBudget:
    total_value_quote: Decimal
    target_base: Decimal
    target_quote: Decimal


@dataclass(frozen=True)
class BudgetAnchor:
    value_quote: Decimal
    wallet_base_amount: Decimal
    wallet_quote_amount: Decimal


@dataclass(frozen=True)
class EntryLog:
    reason: str
    current_price: Optional[Decimal]
    details: Optional[Dict[str, object]] = None


@dataclass(frozen=True)
class Intent:
    flow: IntentFlow
    stage: IntentStage = IntentStage.NONE
    reason: Optional[str] = None


@dataclass
class FeeEstimatorContext:
    fee_rate_ewma: Optional[Decimal] = None
    last_fee_value: Optional[Decimal] = None
    last_fee_ts: Optional[float] = None
    last_position_address: Optional[str] = None


@dataclass
class LpContext:
    anchor: Optional[BudgetAnchor] = None
    open_base: Optional[Decimal] = None
    open_quote: Optional[Decimal] = None
    fee: FeeEstimatorContext = field(default_factory=FeeEstimatorContext)


@dataclass(frozen=True)
class RebalancePlan:
    reopen_after_ts: float


@dataclass
class RebalanceContext:
    plans: Dict[str, RebalancePlan] = field(default_factory=dict)
    last_rebalance_ts: float = 0.0
    timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    out_of_range_since: Dict[str, float] = field(default_factory=dict)


@dataclass
class SwapContext:
    settled_executor_ids: Set[str] = field(default_factory=set)
    last_inventory_swap_ts: float = 0.0
    inventory_swap_failed: Optional[bool] = None
    awaiting_balance_refresh: bool = False


@dataclass
class StopLossContext:
    until_ts: float = 0.0
    pending_liquidation: bool = False
    liquidation_target_base: Optional[Decimal] = None
    last_liquidation_attempt_ts: float = 0.0
    last_exit_reason: Optional[str] = None


@dataclass
class FailureContext:
    blocked: bool = False
    reason: Optional[str] = None


@dataclass
class ControllerContext:
    lp: Dict[str, LpContext] = field(default_factory=dict)
    rebalance: RebalanceContext = field(default_factory=RebalanceContext)
    swap: SwapContext = field(default_factory=SwapContext)
    stoploss: StopLossContext = field(default_factory=StopLossContext)
    failure: FailureContext = field(default_factory=FailureContext)


@dataclass
class DecisionPatch:
    set_failure_reason: Optional[str] = None
    clear_rebalance_all: bool = False
    add_rebalance_plans: Dict[str, RebalancePlan] = field(default_factory=dict)
    clear_rebalance_plans: Set[str] = field(default_factory=set)
    clear_out_of_range_since: Set[str] = field(default_factory=set)
    update_out_of_range_since: Dict[str, float] = field(default_factory=dict)
    record_rebalance_ts: Optional[float] = None

    set_stoploss_until_ts: Optional[float] = None
    set_stoploss_last_exit_reason: Optional[str] = None
    set_stoploss_pending_liquidation: Optional[bool] = None
    set_stoploss_liquidation_target_base: Optional[Decimal] = None
    set_stoploss_last_liquidation_attempt_ts: Optional[float] = None

    set_swap_last_inventory_swap_ts: Optional[float] = None
    set_swap_inventory_swap_failed: Optional[bool] = None

    set_lp_open_amounts: Dict[str, Tuple[Decimal, Decimal]] = field(default_factory=dict)


@dataclass
class Decision:
    intent: Intent
    actions: List[ExecutorAction] = field(default_factory=list)
    entry_log: Optional[EntryLog] = None
    patch: DecisionPatch = field(default_factory=DecisionPatch)

