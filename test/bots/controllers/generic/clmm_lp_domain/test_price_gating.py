import os
import sys
import types
from decimal import Decimal

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates

# ---- Import target modules (after stubs) ----
sys.modules.pop("bots.controllers.generic.clmm_lp_domain.components", None)
from bots.controllers.generic.clmm_lp_domain.components import ControllerContext, LPView, Snapshot
from bots.controllers.generic.clmm_lp_domain.exit_policy import ExitPolicy
from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
from bots.controllers.generic.clmm_lp_domain.rebalance_engine import RebalanceEngine


class DummyConfig:
    def __init__(self):
        self.id = "test"
        self.manual_kill_switch = False
        self.rebalance_enabled = True
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
        self.reenter_enabled = True


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "price_unavailable"


def _estimate_position_value(_lp, _price):
    return Decimal("100")


def _make_lp_view(state=None, lower=Decimal("100"), upper=Decimal("150")):
    return LPView(
        executor_id="lp1",
        is_active=True,
        is_done=False,
        close_type=None,
        state=state,
        position_address="addr",
        base_amount=Decimal("1"),
        quote_amount=Decimal("1"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=lower,
        upper_price=upper,
        out_of_range_since=None,
    )


def _make_snapshot(now: float, price: Decimal | None, lp_view: LPView):
    return Snapshot(
        now=now,
        current_price=price,
        balance_fresh=True,
        wallet_base=Decimal("0"),
        wallet_quote=Decimal("0"),
        lp={lp_view.executor_id: lp_view},
        swaps={},
        active_lp=[lp_view],
        active_swaps=[],
    )


def test_rebalance_engine_uses_price_not_executor_state():
    config = DummyConfig()
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    lp_view = _make_lp_view(state=None, lower=Decimal("100"), upper=Decimal("150"))
    snapshot = _make_snapshot(now=1000, price=Decimal("200"), lp_view=lp_view)
    ctx = ControllerContext()
    ctx.out_of_range_since = 0.0

    signal = engine.evaluate(snapshot, ctx, lp_view)

    assert signal.should_rebalance is True


def test_rebalance_disabled_when_flag_missing():
    config = types.SimpleNamespace(
        id="test",
        manual_kill_switch=False,
        rebalance_seconds=10,
        hysteresis_pct=Decimal("0"),
        cooldown_seconds=0,
        max_rebalances_per_hour=0,
    )
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    lp_view = _make_lp_view(state=None, lower=Decimal("100"), upper=Decimal("150"))
    snapshot = _make_snapshot(now=1500, price=Decimal("200"), lp_view=lp_view)
    ctx = ControllerContext()
    ctx.out_of_range_since = 0.0

    signal = engine.evaluate(snapshot, ctx, lp_view)

    assert signal.should_rebalance is False
    assert signal.reason == "rebalance_disabled"


def test_out_of_range_timer_uses_price_bounds_when_state_missing():
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
    lp_view = _make_lp_view(state=None, lower=Decimal("100"), upper=Decimal("150"))
    snapshot = _make_snapshot(now=2000, price=Decimal("90"), lp_view=lp_view)
    ctx = ControllerContext()

    fsm._update_out_of_range_timer(snapshot, ctx, lp_view)

    assert ctx.out_of_range_since == snapshot.now


def test_price_unavailable_freezes_active_state_and_clears_timer():
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
    lp_view = _make_lp_view(state=LPPositionStates.OUT_OF_RANGE.value, lower=Decimal("100"), upper=Decimal("150"))
    snapshot = _make_snapshot(now=3000, price=None, lp_view=lp_view)
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE
    ctx.out_of_range_since = 1234.0

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "price_unavailable"
    assert ctx.out_of_range_since is None
    assert ctx.state == ctx.state.ACTIVE
