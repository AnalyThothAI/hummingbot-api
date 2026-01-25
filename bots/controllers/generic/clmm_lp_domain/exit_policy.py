from decimal import Decimal
from typing import Optional


class ExitPolicy:
    def __init__(self, *, config) -> None:
        self._config = config

    def should_stoploss(
        self,
        anchor_value_quote: Optional[Decimal],
        equity: Optional[Decimal],
    ) -> bool:
        if self._config.stop_loss_pnl_pct <= 0:
            return False
        if anchor_value_quote is None or anchor_value_quote <= 0:
            return False
        if equity is None:
            return False
        trigger_level = anchor_value_quote - (anchor_value_quote * self._config.stop_loss_pnl_pct)
        return equity <= trigger_level

    def should_take_profit(
        self,
        anchor_value_quote: Optional[Decimal],
        equity: Optional[Decimal],
    ) -> bool:
        if self._config.take_profit_pnl_pct <= 0:
            return False
        if anchor_value_quote is None or anchor_value_quote <= 0:
            return False
        if equity is None:
            return False
        trigger_level = anchor_value_quote + (anchor_value_quote * self._config.take_profit_pnl_pct)
        return equity >= trigger_level
