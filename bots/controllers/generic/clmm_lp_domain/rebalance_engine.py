from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Tuple

from .components import ControllerContext, LPView, Snapshot
from .cost_filter import CostFilter

EstimatePositionValue = Callable[[LPView, Decimal], Decimal]


@dataclass(frozen=True)
class RebalanceSignal:
    should_rebalance: bool
    reason: str = ""


class RebalanceEngine:
    def __init__(self, *, config, estimate_position_value: EstimatePositionValue) -> None:
        self._config = config
        self._estimate_position_value = estimate_position_value

    def evaluate(self, snapshot: Snapshot, ctx: ControllerContext, lp_view: LPView) -> RebalanceSignal:
        now = snapshot.now
        lower_price = lp_view.lower_price
        upper_price = lp_view.upper_price
        if lower_price is None or upper_price is None or lower_price <= 0 or upper_price <= 0:
            return RebalanceSignal(False, "bounds_unavailable")

        effective_price = snapshot.current_price if snapshot.current_price is not None else lp_view.current_price
        if effective_price is None or effective_price <= 0:
            return RebalanceSignal(False, "price_unavailable")

        if lower_price <= effective_price <= upper_price:
            return RebalanceSignal(False, "in_range")

        deviation_pct = self._out_of_range_deviation_pct(effective_price, lower_price, upper_price)
        hysteresis_pct = max(Decimal("0"), self._config.hysteresis_pct)
        if deviation_pct < (hysteresis_pct * Decimal("100")):
            return RebalanceSignal(False, "hysteresis_guard")

        out_of_range_since = ctx.out_of_range_since
        if out_of_range_since is None:
            return RebalanceSignal(False, "out_of_range_timer_missing")
        if (now - out_of_range_since) < self._config.rebalance_seconds:
            return RebalanceSignal(False, "out_of_range_wait")
        if (now - ctx.last_rebalance_ts) < self._config.cooldown_seconds:
            return RebalanceSignal(False, "cooldown")
        if not self._can_rebalance_now(now, ctx):
            return RebalanceSignal(False, "max_rebalances")

        allow_rebalance = CostFilter.allow_rebalance(
            enabled=self._config.cost_filter_enabled,
            position_value=self._estimate_position_value(lp_view, effective_price),
            fee_rate_ewma=ctx.fee.fee_rate_ewma,
            fee_rate_bootstrap_quote_per_hour=self._config.cost_filter_fee_rate_bootstrap_quote_per_hour,
            auto_swap_enabled=self._config.auto_swap_enabled,
            swap_slippage_pct=max(Decimal("0"), self._config.swap_slippage_pct) * Decimal("100"),
            fixed_cost_quote=self._config.cost_filter_fixed_cost_quote,
            max_payback_sec=self._config.cost_filter_max_payback_sec,
        )
        if not allow_rebalance and CostFilter.should_force_rebalance(
            now=now,
            out_of_range_since=out_of_range_since,
            rebalance_seconds=self._config.rebalance_seconds,
        ):
            allow_rebalance = True
        if not allow_rebalance:
            return RebalanceSignal(False, "cost_filter")

        return RebalanceSignal(True, "out_of_range_rebalance")

    def record_rebalance(self, now: float, ctx: ControllerContext) -> None:
        ctx.last_rebalance_ts = now
        ctx.rebalance_timestamps.append(now)
        ctx.rebalance_count += 1

    def _can_rebalance_now(self, now: float, ctx: ControllerContext) -> bool:
        if self._config.max_rebalances_per_hour <= 0:
            return True
        while ctx.rebalance_timestamps and (now - ctx.rebalance_timestamps[0] > 3600):
            ctx.rebalance_timestamps.popleft()
        return len(ctx.rebalance_timestamps) < self._config.max_rebalances_per_hour

    @staticmethod
    def _out_of_range_deviation_pct(price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        if price < lower:
            return (lower - price) / lower * Decimal("100")
        if price > upper:
            return (price - upper) / upper * Decimal("100")
        return Decimal("0")
