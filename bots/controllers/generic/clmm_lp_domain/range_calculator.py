from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor, log
from typing import Optional, Tuple


@dataclass(frozen=True)
class RangePlan:
    center_price: Decimal
    lower: Decimal
    upper: Decimal


class RangeCalculator:
    @staticmethod
    def geometric_bounds(center_price: Decimal, width_pct: Decimal) -> Optional[Tuple[Decimal, Decimal]]:
        if center_price <= 0:
            return None
        width = max(Decimal("0"), width_pct) / Decimal("100")
        factor = (Decimal("1") + width).sqrt()
        lower = center_price / factor
        upper = center_price * factor
        if lower <= 0 or upper <= 0 or lower >= upper:
            return None
        return lower, upper

    @staticmethod
    def geometric_plan(center_price: Decimal, width_pct: Decimal) -> Optional[RangePlan]:
        bounds = RangeCalculator.geometric_bounds(center_price, width_pct)
        if bounds is None:
            return None
        return RangePlan(center_price=center_price, lower=bounds[0], upper=bounds[1])

    @staticmethod
    def align_bounds_to_ticks(
        lower: Decimal,
        upper: Decimal,
        *,
        tick_spacing: int,
        tick_base: Decimal,
    ) -> Optional[Tuple[Decimal, Decimal]]:
        if lower <= 0 or upper <= 0 or lower >= upper:
            return None
        if tick_spacing <= 0 or tick_base <= 1:
            return None
        lower_tick = RangeCalculator._price_to_tick(lower, tick_base)
        upper_tick = RangeCalculator._price_to_tick(upper, tick_base)
        aligned_lower_tick = int(floor(lower_tick / tick_spacing) * tick_spacing)
        aligned_upper_tick = int(ceil(upper_tick / tick_spacing) * tick_spacing)
        if aligned_lower_tick >= aligned_upper_tick:
            return None
        aligned_lower = RangeCalculator._tick_to_price(aligned_lower_tick, tick_base)
        aligned_upper = RangeCalculator._tick_to_price(aligned_upper_tick, tick_base)
        if aligned_lower <= 0 or aligned_upper <= 0 or aligned_lower >= aligned_upper:
            return None
        return aligned_lower, aligned_upper

    @staticmethod
    def clamp_price_by_ticks(
        price: Decimal,
        lower: Decimal,
        upper: Decimal,
        *,
        tick_base: Decimal,
        clamp_ticks: int,
    ) -> Optional[Decimal]:
        if price <= 0 or lower <= 0 or upper <= 0 or lower >= upper:
            return None
        if clamp_ticks <= 0:
            return price
        if tick_base <= 1:
            return None
        lower_tick = RangeCalculator._price_to_tick(lower, tick_base)
        upper_tick = RangeCalculator._price_to_tick(upper, tick_base)
        clamp_lower_tick = lower_tick + clamp_ticks
        clamp_upper_tick = upper_tick - clamp_ticks
        if clamp_lower_tick >= clamp_upper_tick:
            return None
        clamp_lower = RangeCalculator._tick_to_price(int(floor(clamp_lower_tick)), tick_base)
        clamp_upper = RangeCalculator._tick_to_price(int(ceil(clamp_upper_tick)), tick_base)
        if clamp_lower >= clamp_upper:
            return None
        return min(max(price, clamp_lower), clamp_upper)

    @staticmethod
    def _price_to_tick(price: Decimal, tick_base: Decimal) -> float:
        return log(float(price)) / log(float(tick_base))

    @staticmethod
    def _tick_to_price(tick: int, tick_base: Decimal) -> Decimal:
        return Decimal(str(float(tick_base) ** tick))
