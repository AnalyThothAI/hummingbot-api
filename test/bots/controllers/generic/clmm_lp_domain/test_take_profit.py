import os
import sys
from decimal import Decimal

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction

# ---- Import target modules (after stubs) ----
for module_name in (
    "bots.controllers.generic.clmm_lp_domain.components",
    "bots.controllers.generic.clmm_lp_domain.clmm_fsm",
    "bots.controllers.generic.clmm_lp_domain.rebalance_engine",
    "bots.controllers.generic.clmm_lp_domain.exit_policy",
):
    sys.modules.pop(module_name, None)
from bots.controllers.generic.clmm_lp_domain.components import ControllerContext, LPView, Snapshot
from bots.controllers.generic.clmm_lp_domain.exit_policy import ExitPolicy
from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
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
        self.take_profit_pnl_pct = Decimal("0.1")
        self.stop_loss_pause_sec = 0
        self.reenter_enabled = True
        self.position_value_quote = Decimal("0")
        self.target_price = Decimal("0")
        self.trigger_above = True
        self.balance_update_timeout_sec = 10
        self.balance_refresh_timeout_sec = 30


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "price_unavailable"


def _estimate_position_value(lp, _price):
    return lp.base_amount


def _make_lp_view(
    state=None,
    lower=Decimal("0.5"),
    upper=Decimal("2"),
    done=False,
    position=True,
    base_amount=Decimal("0"),
):
    return LPView(
        executor_id="lp1",
        is_active=True,
        is_done=done,
        close_type=None,
        state=state,
        position_address="addr" if position else None,
        base_amount=base_amount,
        quote_amount=Decimal("0"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=lower,
        upper_price=upper,
        out_of_range_since=None,
    )


def _make_snapshot(now: float, price: Decimal | None, lp_view: LPView, wallet_quote=Decimal("0")):
    return Snapshot(
        now=now,
        current_price=price,
        balance_fresh=True,
        wallet_base=Decimal("0"),
        wallet_quote=wallet_quote,
        lp={lp_view.executor_id: lp_view},
        swaps={},
        active_lp=[lp_view],
        active_swaps=[],
    )


def test_rebalance_disabled_blocks_signal():
    config = DummyConfig()
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    lp_view = _make_lp_view(state=None, lower=Decimal("100"), upper=Decimal("150"))
    snapshot = _make_snapshot(now=1000, price=Decimal("200"), lp_view=lp_view)
    ctx = ControllerContext()
    ctx.out_of_range_since = 0.0

    signal = engine.evaluate(snapshot, ctx, lp_view)

    assert signal.should_rebalance is False
    assert signal.reason == "rebalance_disabled"


def test_take_profit_triggers_close_action():
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
    lp_view = _make_lp_view(state=LPPositionStates.IN_RANGE.value, base_amount=Decimal("120"))
    snapshot = _make_snapshot(now=2000, price=Decimal("1"), lp_view=lp_view, wallet_quote=Decimal("0"))
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE
    ctx.anchor_value_quote = Decimal("100")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "take_profit"
    assert decision.actions and isinstance(decision.actions[0], StopExecutorAction)
    assert ctx.state.value == "TAKE_PROFIT_STOP"


def test_take_profit_ignores_wallet_excess_under_risk_cap():
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
    lp_view = _make_lp_view(state=LPPositionStates.IN_RANGE.value, base_amount=Decimal("80"))
    snapshot = _make_snapshot(now=2500, price=Decimal("1"), lp_view=lp_view, wallet_quote=Decimal("50"))
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE
    ctx.anchor_value_quote = Decimal("100")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "active"
    assert ctx.state.value == "ACTIVE"

def test_take_profit_stop_transitions_to_idle_after_close():
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
    lp_view = _make_lp_view(state=LPPositionStates.COMPLETE.value, done=True, position=False)
    snapshot = _make_snapshot(now=3000, price=Decimal("1"), lp_view=lp_view)
    ctx = ControllerContext()
    ctx.state = ctx.state.TAKE_PROFIT_STOP
    ctx.pending_close_lp_id = "lp1"
    ctx.anchor_value_quote = Decimal("100")
    ctx.pending_realized_anchor = Decimal("100")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "take_profit_closed"
    assert ctx.state.value == "IDLE"


def test_take_profit_stop_transitions_to_exit_swap_when_enabled():
    config = DummyConfig()
    config.exit_full_liquidation = True
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    fsm = CLMMFSM(
        config=config,
        action_factory=None,
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=engine,
        exit_policy=ExitPolicy(config=config),
    )
    lp_view = _make_lp_view(state=LPPositionStates.COMPLETE.value, done=True, position=False)
    snapshot = _make_snapshot(now=3100, price=Decimal("1"), lp_view=lp_view)
    ctx = ControllerContext()
    ctx.state = ctx.state.TAKE_PROFIT_STOP
    ctx.pending_close_lp_id = "lp1"
    ctx.anchor_value_quote = Decimal("100")
    ctx.pending_realized_anchor = Decimal("100")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "take_profit_exit_swap"
    assert ctx.state.value == "EXIT_SWAP"
