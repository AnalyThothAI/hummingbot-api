import os
import sys
from decimal import Decimal

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates

from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
from bots.controllers.generic.clmm_lp_domain.components import ControllerContext, LPView, Snapshot
from bots.controllers.generic.clmm_lp_domain.exit_policy import ExitPolicy
from bots.controllers.generic.clmm_lp_domain.rebalance_engine import RebalanceEngine


class DummyConfig:
    def __init__(self):
        self.id = "test"
        self.manual_kill_switch = False
        self.rebalance_enabled = False
        self.rebalance_seconds = 10
        self.hysteresis_pct = Decimal("0")
        self.cooldown_seconds = 0
        self.max_rebalances_per_hour = 0
        self.rebalance_open_timeout_sec = 0

        self.exit_full_liquidation = False
        self.exit_swap_slippage_pct = Decimal("0")
        self.max_exit_swap_attempts = 0

        self.stop_loss_pnl_pct = Decimal("0")
        self.take_profit_pnl_pct = Decimal("0")
        self.stop_loss_pause_sec = 0
        self.reenter_enabled = True

        self.position_value_quote = Decimal("0")
        self.target_price = Decimal("0")
        self.trigger_above = True

        self.balance_update_timeout_sec = 10
        self.balance_refresh_timeout_sec = 30


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "price_unavailable"


def _estimate_position_value(lp: LPView, price: Decimal) -> Decimal:
    return (abs(lp.base_amount) + abs(lp.base_fee)) * price + (abs(lp.quote_amount) + abs(lp.quote_fee))


def _make_snapshot(*, now: float, price: Decimal, lp_view: LPView) -> Snapshot:
    return Snapshot(
        now=now,
        current_price=price,
        balance_fresh=True,
        wallet_base=Decimal("0"),
        wallet_quote=Decimal("0"),
        lp={lp_view.executor_id: lp_view},
        swaps={},
        active_lp=[],
        active_swaps=[],
    )


def test_record_realized_on_close_clears_pending_anchor_and_updates_pnl():
    config = DummyConfig()
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    fsm = CLMMFSM(
        config=config,
        action_factory=None,
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=engine,
        exit_policy=ExitPolicy(config=config),
    )

    ctx = ControllerContext()
    ctx.anchor_value_quote = Decimal("100")
    ctx.pending_realized_anchor = Decimal("100")

    lp_view = LPView(
        executor_id="lp1",
        is_active=False,
        is_done=True,
        close_type=None,
        state=LPPositionStates.COMPLETE.value,
        position_address=None,
        base_amount=Decimal("10"),
        quote_amount=Decimal("0"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("1"),
        upper_price=Decimal("2"),
        out_of_range_since=None,
    )
    snapshot = _make_snapshot(now=1000, price=Decimal("2"), lp_view=lp_view)

    fsm._record_realized_on_close(snapshot, ctx, lp_view, reason="stop_loss")

    assert ctx.pending_realized_anchor is None
    assert ctx.realized_volume_quote == Decimal("100")
    assert ctx.realized_pnl_quote == Decimal("20") - Decimal("100")
