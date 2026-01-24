from decimal import Decimal
from typing import Optional

from .components import FeeEstimatorContext


class CostFilter:
    FEE_EWMA_ALPHA = Decimal("0.1")
    FEE_SAMPLE_MIN_SECONDS = Decimal("10")

    IN_RANGE_TIME_SEC = Decimal("3600")
    SWAP_NOTIONAL_PCT = Decimal("0.5")
    SWAP_FEE_BUFFER_PCT = Decimal("0.3")
    FEE_RATE_FLOOR = Decimal("0.000000001")
    SAFETY_FACTOR = Decimal("2")

    FORCE_REBALANCE_MULTIPLIER = 10
    FORCE_REBALANCE_MIN_SEC = 600

    @classmethod
    def update_fee_rate_ewma(
        cls,
        *,
        now: float,
        position_address: str,
        base_fee: Decimal,
        quote_fee: Decimal,
        price: Decimal,
        ctx: FeeEstimatorContext,
    ) -> None:
        if not position_address:
            return

        if ctx.last_position_address != position_address:
            ctx.last_position_address = position_address
            ctx.last_base_fee = None
            ctx.last_quote_fee = None
            ctx.last_fee_ts = None
            ctx.fee_rate_ewma = None
            return

        if ctx.last_fee_ts is None or ctx.last_base_fee is None or ctx.last_quote_fee is None:
            ctx.last_fee_ts = now
            ctx.last_base_fee = base_fee
            ctx.last_quote_fee = quote_fee
            return

        dt = Decimal(str(now - ctx.last_fee_ts))
        if dt <= 0:
            return
        if dt < cls.FEE_SAMPLE_MIN_SECONDS:
            return

        delta_base_fee = base_fee - ctx.last_base_fee
        delta_quote_fee = quote_fee - ctx.last_quote_fee
        if delta_base_fee < 0 or delta_quote_fee < 0:
            ctx.last_fee_ts = now
            ctx.last_base_fee = base_fee
            ctx.last_quote_fee = quote_fee
            return

        delta_fee_quote = (delta_base_fee * price) + delta_quote_fee
        if delta_fee_quote < 0:
            ctx.last_fee_ts = now
            ctx.last_base_fee = base_fee
            ctx.last_quote_fee = quote_fee
            return

        fee_rate = delta_fee_quote / dt
        alpha = cls.FEE_EWMA_ALPHA
        if ctx.fee_rate_ewma is None:
            ctx.fee_rate_ewma = fee_rate
        else:
            ctx.fee_rate_ewma = (ctx.fee_rate_ewma * (Decimal("1") - alpha)) + (fee_rate * alpha)

        ctx.last_fee_ts = now
        ctx.last_base_fee = base_fee
        ctx.last_quote_fee = quote_fee

    @classmethod
    def allow_rebalance(
        cls,
        *,
        enabled: bool,
        position_value: Decimal,
        fee_rate_ewma: Optional[Decimal],
        fee_rate_bootstrap_quote_per_hour: Decimal,
        auto_swap_enabled: bool,
        swap_slippage_pct: Decimal,
        fixed_cost_quote: Decimal,
        max_payback_sec: int,
    ) -> bool:
        if not enabled:
            return True

        fee_rate = fee_rate_ewma
        if fee_rate is None or fee_rate <= 0:
            if fee_rate_bootstrap_quote_per_hour <= 0:
                return True
            fee_rate = fee_rate_bootstrap_quote_per_hour / Decimal("3600")

        expected_fees = fee_rate * cls.IN_RANGE_TIME_SEC
        fixed_cost = max(Decimal("0"), fixed_cost_quote)
        swap_notional = position_value * (cls.SWAP_NOTIONAL_PCT if auto_swap_enabled else Decimal("0"))
        swap_fee_pct = max(Decimal("0"), swap_slippage_pct + cls.SWAP_FEE_BUFFER_PCT)
        swap_cost = swap_notional * (swap_fee_pct / Decimal("100"))
        cost = fixed_cost + swap_cost

        if cost <= 0:
            return True

        if expected_fees < (cost * cls.SAFETY_FACTOR):
            return False

        min_fee_rate = max(Decimal("0"), cls.FEE_RATE_FLOOR)
        payback_sec = cost / max(fee_rate, min_fee_rate)
        return payback_sec <= Decimal(str(max_payback_sec))

    @classmethod
    def should_force_rebalance(cls, *, now: float, out_of_range_since: float, rebalance_seconds: int) -> bool:
        if rebalance_seconds <= 0:
            return False
        threshold = max(
            rebalance_seconds * cls.FORCE_REBALANCE_MULTIPLIER,
            cls.FORCE_REBALANCE_MIN_SEC,
        )
        return (now - out_of_range_since) >= threshold
