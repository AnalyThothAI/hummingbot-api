import os
import sys

from bots.controllers.generic.clmm_lp_base import CLMMLPBaseConfig


def test_exit_full_liquidation_defaults_true_when_stoploss_set_and_field_omitted():
    cfg = CLMMLPBaseConfig(stop_loss_pnl_pct="0.10")
    assert cfg.exit_full_liquidation is True


def test_exit_full_liquidation_respects_explicit_false_even_when_stoploss_set():
    cfg = CLMMLPBaseConfig(stop_loss_pnl_pct="0.10", exit_full_liquidation=False)
    assert cfg.exit_full_liquidation is False


def test_exit_full_liquidation_stays_false_when_stoploss_not_set_and_field_omitted():
    cfg = CLMMLPBaseConfig(stop_loss_pnl_pct="0")
    assert cfg.exit_full_liquidation is False
