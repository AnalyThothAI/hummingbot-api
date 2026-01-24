from decimal import Decimal
from typing import Optional, Tuple


class V3Math:
    @staticmethod
    def quote_per_base_ratio(
        price: Decimal,
        lower: Decimal,
        upper: Decimal,
    ) -> Optional[Decimal]:
        if price <= 0 or lower <= 0 or upper <= 0:
            return None
        if lower >= upper:
            return None
        if not (lower < price < upper):
            return None
        sqrt_p = price.sqrt()
        sqrt_a = lower.sqrt()
        sqrt_b = upper.sqrt()
        denom = sqrt_b - sqrt_p
        numer = sqrt_p * sqrt_b * (sqrt_p - sqrt_a)
        if denom <= 0 or numer <= 0:
            return None
        ratio = numer / denom
        return ratio if ratio > 0 else None

    @staticmethod
    def target_amounts_from_value(
        value_quote: Decimal,
        price: Decimal,
        ratio_quote_per_base: Decimal,
    ) -> Optional[Tuple[Decimal, Decimal]]:
        if value_quote <= 0 or price <= 0 or ratio_quote_per_base <= 0:
            return None
        base_amount = value_quote / (price + ratio_quote_per_base)
        if base_amount <= 0:
            return None
        quote_amount = value_quote - (base_amount * price)
        if quote_amount < 0:
            return None
        return base_amount, quote_amount
