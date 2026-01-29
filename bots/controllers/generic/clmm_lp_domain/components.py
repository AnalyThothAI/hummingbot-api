from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction


class ControllerState(str, Enum):
    IDLE = "IDLE"
    ENTRY_OPEN = "ENTRY_OPEN"
    ENTRY_SWAP = "ENTRY_SWAP"
    ACTIVE = "ACTIVE"
    REBALANCE_STOP = "REBALANCE_STOP"
    REBALANCE_SWAP = "REBALANCE_SWAP"
    REBALANCE_OPEN = "REBALANCE_OPEN"
    STOPLOSS_STOP = "STOPLOSS_STOP"
    STOPLOSS_SWAP = "STOPLOSS_SWAP"
    COOLDOWN = "COOLDOWN"


class SwapPurpose(str, Enum):
    INVENTORY = "inventory"
    INVENTORY_REBALANCE = "inventory_rebalance"
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

    def executor_token_order_inverted(self, executor) -> Optional[bool]:
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
    base_amount: Decimal
    quote_amount: Decimal
    base_fee: Decimal
    quote_fee: Decimal
    lower_price: Optional[Decimal]
    upper_price: Optional[Decimal]
    out_of_range_since: Optional[float]


@dataclass(frozen=True)
class SwapView:
    executor_id: str
    is_active: bool
    is_done: bool
    close_type: Optional[CloseType]
    timestamp: float
    level_id: Optional[str]
    purpose: Optional[SwapPurpose]
    amount: Decimal


@dataclass(frozen=True)
class Snapshot:
    now: float
    current_price: Optional[Decimal]
    balance_fresh: bool
    wallet_base: Decimal
    wallet_quote: Decimal
    lp: Dict[str, LPView]
    swaps: Dict[str, SwapView]
    active_lp: List[LPView]
    active_swaps: List[SwapView]


@dataclass(frozen=True)
class OpenProposal:
    lower: Decimal
    upper: Decimal
    target_base: Decimal
    target_quote: Decimal
    delta_base: Decimal
    delta_quote_value: Decimal
    open_base: Decimal
    open_quote: Decimal
    min_swap_value_quote: Decimal


@dataclass
class FeeEstimatorContext:
    fee_rate_ewma: Optional[Decimal] = None
    last_base_fee: Optional[Decimal] = None
    last_quote_fee: Optional[Decimal] = None
    last_fee_ts: Optional[float] = None
    last_position_address: Optional[str] = None


@dataclass
class ControllerContext:
    state: ControllerState = ControllerState.IDLE
    state_since_ts: float = 0.0
    cooldown_until_ts: float = 0.0
    domain_ready: bool = True
    domain_error: Optional[str] = None
    domain_resolved_ts: float = 0.0
    last_tick_ts: float = 0.0
    anchor_value_quote: Optional[Decimal] = None
    last_rebalance_ts: float = 0.0
    rebalance_timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    rebalance_count: int = 0
    rebalance_signal_reason: Optional[str] = None
    pending_lp_id: Optional[str] = None
    pending_swap_id: Optional[str] = None
    pending_swap_since_ts: float = 0.0
    inventory_swap_attempts: int = 0
    last_inventory_swap_ts: float = 0.0
    normalization_swap_attempts: int = 0
    last_normalization_swap_ts: float = 0.0
    stoploss_swap_attempts: int = 0
    last_stoploss_swap_ts: float = 0.0
    last_exit_reason: Optional[str] = None
    last_decision_reason: Optional[str] = None
    out_of_range_since: Optional[float] = None
    realized_pnl_quote: Decimal = Decimal("0")
    realized_volume_quote: Decimal = Decimal("0")
    pending_realized_anchor: Optional[Decimal] = None
    force_balance_refresh_until_ts: float = 0.0
    force_balance_refresh_reason: Optional[str] = None
    stoploss_balance_refresh_attempts: int = 0
    fee: FeeEstimatorContext = field(default_factory=FeeEstimatorContext)


@dataclass(frozen=True)
class Decision:
    actions: List[ExecutorAction] = field(default_factory=list)
    next_state: Optional[ControllerState] = None
    reason: str = ""
