from decimal import Decimal
from typing import Optional

from .components import pct_to_ratio


class ExitPolicy:
    def __init__(self, *, config) -> None:
        self._config = config

    def should_stoploss(
        self,
        anchor_value_quote: Optional[Decimal],
        equity: Optional[Decimal],
    ) -> bool:
        stop_loss_ratio = pct_to_ratio(getattr(self._config, "stop_loss_pnl_pct", Decimal("0")))
        if stop_loss_ratio <= 0:
            return False
        if anchor_value_quote is None or anchor_value_quote <= 0:
            return False
        if equity is None:
            return False
        trigger_level = anchor_value_quote - (anchor_value_quote * stop_loss_ratio)
        return equity <= trigger_level

    def should_take_profit(
        self,
        anchor_value_quote: Optional[Decimal],
        equity: Optional[Decimal],
    ) -> bool:
        take_profit_ratio = pct_to_ratio(getattr(self._config, "take_profit_pnl_pct", Decimal("0")))
        if take_profit_ratio <= 0:
            return False
        if anchor_value_quote is None or anchor_value_quote <= 0:
            return False
        if equity is None:
            return False
        trigger_level = anchor_value_quote + (anchor_value_quote * take_profit_ratio)
        return equity >= trigger_level
