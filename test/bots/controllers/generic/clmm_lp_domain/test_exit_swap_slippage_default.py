from decimal import Decimal

from bots.controllers.generic.clmm_lp_base import CLMMLPBaseConfig


def test_exit_swap_slippage_pct_defaults_to_2_percent_ratio():
    cfg = CLMMLPBaseConfig()
    assert cfg.exit_swap_slippage_pct == Decimal("0.02")


def test_exit_swap_slippage_pct_respects_explicit_value():
    cfg = CLMMLPBaseConfig(exit_swap_slippage_pct="0.05")
    assert cfg.exit_swap_slippage_pct == Decimal("0.05")

