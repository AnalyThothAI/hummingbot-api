from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, List, Optional, Set, Tuple

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


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


class SwapPurpose(str, Enum):
    INVENTORY = "inventory"
    STOPLOSS = "liquidate"


@dataclass(frozen=True)
class PoolDomainAdapter:
    trading_pair: str
    pool_trading_pair: str
    base_token: str
    quote_token: str
    pool_base_token: str
    pool_quote_token: str
    pool_order_inverted: bool

    @classmethod
    def from_config(cls, trading_pair: str, pool_trading_pair: Optional[str]) -> "PoolDomainAdapter":
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
    def pool_amounts_to_strategy(lp_base: Decimal, lp_quote: Decimal, inverted: bool) -> Tuple[Decimal, Decimal]:
        return (lp_quote, lp_base) if inverted else (lp_base, lp_quote)

    def strategy_amounts_to_pool(self, base_amt: Decimal, quote_amt: Decimal) -> Tuple[Decimal, Decimal]:
        return (quote_amt, base_amt) if self.pool_order_inverted else (base_amt, quote_amt)

    @staticmethod
    def pool_price_to_strategy(price: Decimal, inverted: bool) -> Decimal:
        if not inverted:
            return price
        if price <= 0:
            return price
        return Decimal("1") / price

    def strategy_price_to_pool(self, price: Decimal) -> Decimal:
        if not self.pool_order_inverted:
            return price
        if price <= 0:
            return price
        return Decimal("1") / price

    @staticmethod
    def pool_bounds_to_strategy(lower: Decimal, upper: Decimal, inverted: bool) -> Tuple[Decimal, Decimal]:
        if not inverted:
            return lower, upper
        if lower <= 0 or upper <= 0:
            return lower, upper
        mapped_lower = Decimal("1") / upper
        mapped_upper = Decimal("1") / lower
        if mapped_lower > mapped_upper:
            mapped_lower, mapped_upper = mapped_upper, mapped_lower
        return mapped_lower, mapped_upper

    def strategy_bounds_to_pool(self, lower: Decimal, upper: Decimal) -> Tuple[Decimal, Decimal]:
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
    purpose: Optional[SwapPurpose]
    amount: Decimal
    executed_amount_base: Optional[Decimal] = None
    executed_amount_quote: Optional[Decimal] = None
    amount_in: Optional[Decimal] = None
    amount_out: Optional[Decimal] = None
    amount_in_is_quote: Optional[bool] = None


@dataclass(frozen=True)
class Snapshot:
    now: float
    current_price: Optional[Decimal]
    wallet_base: Decimal
    wallet_quote: Decimal
    lp: Dict[str, LPView]
    swaps: Dict[str, SwapView]
    active_lp: List[LPView]
    active_swaps: List[SwapView]


@dataclass(frozen=True)
class Regions:
    manual_stop: bool
    failure_blocked: bool
    has_active_swaps: bool
    active_swap_label: Optional[str]
    has_active_lp: bool
    awaiting_balance_refresh: bool
    stoploss_cooldown_active: bool
    stoploss_pending_liquidation: bool
    rebalance_pending: bool
    rebalance_open_in_progress: bool
    entry_triggered: bool
    reenter_blocked: bool


@dataclass(frozen=True)
class BudgetAnchor:
    value_quote: Decimal
    wallet_base_amount: Decimal
    wallet_quote_amount: Decimal


@dataclass(frozen=True)
class Intent:
    flow: IntentFlow
    stage: IntentStage = IntentStage.NONE
    reason: Optional[str] = None


@dataclass
class FeeEstimatorContext:
    fee_rate_ewma: Optional[Decimal] = None
    last_base_fee: Optional[Decimal] = None
    last_quote_fee: Optional[Decimal] = None
    last_fee_ts: Optional[float] = None
    last_position_address: Optional[str] = None


@dataclass
class LpContext:
    anchor: Optional[BudgetAnchor] = None
    fee: FeeEstimatorContext = field(default_factory=FeeEstimatorContext)


class RebalanceStage(str, Enum):
    STOP_REQUESTED = "STOP_REQUESTED"
    WAIT_REOPEN = "WAIT_REOPEN"
    WAIT_SWAP = "WAIT_SWAP"
    OPEN_REQUESTED = "OPEN_REQUESTED"


@dataclass(frozen=True)
class RebalancePlan:
    stage: RebalanceStage
    reopen_after_ts: float = 0.0
    open_executor_id: Optional[str] = None
    requested_at_ts: float = 0.0


@dataclass
class RebalanceContext:
    plans: Dict[str, RebalancePlan] = field(default_factory=dict)
    last_rebalance_ts: float = 0.0
    timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=200))


@dataclass
class SwapContext:
    settled_executor_ids: Set[str] = field(default_factory=set)
    last_inventory_swap_ts: float = 0.0
    awaiting_balance_refresh: bool = False
    awaiting_balance_refresh_since: float = 0.0


@dataclass
class StopLossContext:
    until_ts: float = 0.0
    pending_liquidation: bool = False  # True when additional base must be sold to complete stoploss liquidation.
    liquidation_target_base: Optional[Decimal] = None  # Remaining base amount to liquidate.
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

    def apply(self, patch: "DecisionPatch") -> None:
        if patch.failure.set_reason is not None:
            self.failure.blocked = True
            self.failure.reason = patch.failure.set_reason

        if patch.rebalance.clear_all:
            self.rebalance.plans.clear()
        if patch.rebalance.add_plans:
            self.rebalance.plans.update(patch.rebalance.add_plans)
        for executor_id in patch.rebalance.clear_plans:
            self.rebalance.plans.pop(executor_id, None)
        if patch.rebalance.record_rebalance_ts is not None:
            ts = patch.rebalance.record_rebalance_ts
            self.rebalance.last_rebalance_ts = ts
            self.rebalance.timestamps.append(ts)

        if patch.stoploss.until_ts is not None:
            self.stoploss.until_ts = patch.stoploss.until_ts
        if patch.stoploss.last_exit_reason is not None:
            self.stoploss.last_exit_reason = patch.stoploss.last_exit_reason
        if patch.stoploss.pending_liquidation is not None:
            self.stoploss.pending_liquidation = patch.stoploss.pending_liquidation
        if patch.stoploss.liquidation_target_base is not None:
            self.stoploss.liquidation_target_base = patch.stoploss.liquidation_target_base
        if patch.stoploss.last_liquidation_attempt_ts is not None:
            self.stoploss.last_liquidation_attempt_ts = patch.stoploss.last_liquidation_attempt_ts

        if patch.stoploss.pending_liquidation is False:
            self.stoploss.liquidation_target_base = None

        if patch.swap.last_inventory_swap_ts is not None:
            self.swap.last_inventory_swap_ts = patch.swap.last_inventory_swap_ts
        if patch.swap.awaiting_balance_refresh is not None:
            self.swap.awaiting_balance_refresh = patch.swap.awaiting_balance_refresh
            if not self.swap.awaiting_balance_refresh:
                self.swap.awaiting_balance_refresh_since = 0.0
        if patch.swap.awaiting_balance_refresh_since is not None:
            self.swap.awaiting_balance_refresh_since = patch.swap.awaiting_balance_refresh_since


@dataclass
class FailurePatch:
    set_reason: Optional[str] = None


@dataclass
class RebalancePatch:
    clear_all: bool = False
    add_plans: Dict[str, RebalancePlan] = field(default_factory=dict)
    clear_plans: Set[str] = field(default_factory=set)
    record_rebalance_ts: Optional[float] = None


@dataclass
class StopLossPatch:
    until_ts: Optional[float] = None
    last_exit_reason: Optional[str] = None
    pending_liquidation: Optional[bool] = None
    liquidation_target_base: Optional[Decimal] = None
    last_liquidation_attempt_ts: Optional[float] = None


@dataclass
class SwapPatch:
    awaiting_balance_refresh: Optional[bool] = None
    last_inventory_swap_ts: Optional[float] = None
    awaiting_balance_refresh_since: Optional[float] = None


@dataclass
class DecisionPatch:
    failure: FailurePatch = field(default_factory=FailurePatch)
    rebalance: RebalancePatch = field(default_factory=RebalancePatch)
    stoploss: StopLossPatch = field(default_factory=StopLossPatch)
    swap: SwapPatch = field(default_factory=SwapPatch)

@dataclass
class Decision:
    intent: Intent
    actions: List[ExecutorAction] = field(default_factory=list)
    patch: DecisionPatch = field(default_factory=DecisionPatch)
