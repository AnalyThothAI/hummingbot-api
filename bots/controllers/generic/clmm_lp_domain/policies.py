from decimal import Decimal
from typing import Dict, Optional

from .range_calculator import RangeCalculator, RangePlan
from .v3_math import V3Math


class CLMMPolicyBase:
    def __init__(self, config) -> None:
        self._config = config

    async def update(self, connector) -> None:
        return None

    def range_plan(self, center_price: Decimal) -> Optional[RangePlan]:
        raise NotImplementedError

    def quote_per_base_ratio(self, price: Decimal, lower: Decimal, upper: Decimal) -> Optional[Decimal]:
        raise NotImplementedError

    def extra_lp_params(self) -> Optional[Dict]:
        return None

    def is_ready(self) -> bool:
        return True


class UniswapV3Policy(CLMMPolicyBase):
    _tick_base = Decimal("1.0001")

    def __init__(self, config) -> None:
        super().__init__(config)
        self._tick_spacing: Optional[int] = None

    def _pool_order_inverted(self) -> bool:
        trading_pair = getattr(self._config, "trading_pair", "")
        pool_trading_pair = getattr(self._config, "pool_trading_pair", None) or trading_pair
        base, quote = trading_pair.split("-") if "-" in trading_pair else ("", "")
        pool_base, pool_quote = pool_trading_pair.split("-") if "-" in pool_trading_pair else ("", "")
        return pool_base == quote and pool_quote == base

    async def update(self, connector) -> None:
        if connector is None or not self._config.pool_address:
            return None
        getter = getattr(connector, "get_pool_info_by_address", None)
        if getter is None:
            return None
        pool_info = await getter(self._config.pool_address)
        if pool_info is None:
            return None
        tick_spacing = getattr(pool_info, "bin_step", None)
        if tick_spacing is None:
            return None
        try:
            tick_spacing = int(tick_spacing)
        except (TypeError, ValueError):
            return None
        if tick_spacing > 0:
            self._tick_spacing = tick_spacing

    def is_ready(self) -> bool:
        return self._tick_spacing is not None and self._tick_spacing > 0

    def range_plan(self, center_price: Decimal) -> Optional[RangePlan]:
        base_plan = RangeCalculator.geometric_plan(center_price, self._config.position_width_pct)
        if base_plan is None:
            return None
        if self._tick_spacing is None or self._tick_spacing <= 0:
            return None
        lower, upper = base_plan.lower, base_plan.upper
        if self._pool_order_inverted():
            if lower <= 0 or upper <= 0 or lower >= upper:
                return None
            pool_lower = Decimal("1") / upper
            pool_upper = Decimal("1") / lower
            aligned = RangeCalculator.align_bounds_to_ticks(
                pool_lower,
                pool_upper,
                tick_spacing=self._tick_spacing,
                tick_base=self._tick_base,
            )
            if aligned is None:
                return None
            aligned_lower = Decimal("1") / aligned[1]
            aligned_upper = Decimal("1") / aligned[0]
            if aligned_lower >= aligned_upper:
                return None
            return RangePlan(center_price=center_price, lower=aligned_lower, upper=aligned_upper)
        aligned = RangeCalculator.align_bounds_to_ticks(
            lower,
            upper,
            tick_spacing=self._tick_spacing,
            tick_base=self._tick_base,
        )
        if aligned is None:
            return None
        return RangePlan(center_price=center_price, lower=aligned[0], upper=aligned[1])

    def quote_per_base_ratio(self, price: Decimal, lower: Decimal, upper: Decimal) -> Optional[Decimal]:
        if self._tick_spacing is None or self._tick_spacing <= 0:
            return None
        multiplier = max(1, int(self._config.ratio_clamp_tick_multiplier))
        clamp_ticks = self._tick_spacing * multiplier
        if self._pool_order_inverted():
            if price <= 0 or lower <= 0 or upper <= 0 or lower >= upper:
                return None
            pool_price = Decimal("1") / price
            pool_lower = Decimal("1") / upper
            pool_upper = Decimal("1") / lower
            clamped_pool_price = RangeCalculator.clamp_price_by_ticks(
                pool_price,
                pool_lower,
                pool_upper,
                tick_base=self._tick_base,
                clamp_ticks=clamp_ticks,
            )
            if clamped_pool_price is None or clamped_pool_price <= 0:
                return None
            clamped_price = Decimal("1") / clamped_pool_price
        else:
            clamped_price = RangeCalculator.clamp_price_by_ticks(
                price,
                lower,
                upper,
                tick_base=self._tick_base,
                clamp_ticks=clamp_ticks,
            )
            if clamped_price is None:
                return None
        return V3Math.quote_per_base_ratio(clamped_price, lower, upper)


class MeteoraPolicy(CLMMPolicyBase):
    def range_plan(self, center_price: Decimal) -> Optional[RangePlan]:
        return RangeCalculator.geometric_plan(center_price, self._config.position_width_pct)

    def quote_per_base_ratio(self, price: Decimal, lower: Decimal, upper: Decimal) -> Optional[Decimal]:
        buffer_pct = max(Decimal("0"), self._config.ratio_edge_buffer_pct)
        if buffer_pct > 0:
            range_size = upper - lower
            clamp_offset = range_size * buffer_pct
            clamp_lower = lower + clamp_offset
            clamp_upper = upper - clamp_offset
            if clamp_lower >= clamp_upper:
                return None
            price = min(max(price, clamp_lower), clamp_upper)
        return V3Math.quote_per_base_ratio(price, lower, upper)

    def extra_lp_params(self) -> Optional[Dict]:
        strategy_type = self._config.strategy_type
        if strategy_type is None:
            return None
        return {"strategyType": int(strategy_type)}
