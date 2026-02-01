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
        self.cost_filter_enabled = False
        self.cost_filter_fee_rate_bootstrap_quote_per_hour = Decimal("0")
        self.auto_swap_enabled = False
        self.swap_slippage_pct = Decimal("0")
        self.cost_filter_fixed_cost_quote = Decimal("0")
        self.cost_filter_max_payback_sec = 0
        self.stop_loss_pnl_pct = Decimal("0")
        self.take_profit_pnl_pct = Decimal("0")
        self.stop_loss_pause_sec = 0
        self.reenter_enabled = True
        self.max_inventory_swap_attempts = 0
        self.inventory_drift_tolerance_pct = Decimal("0")
        self.normalization_cooldown_sec = 0
        self.normalization_min_value_pct = Decimal("0")
        self.normalization_strict = False
        self.max_stoploss_liquidation_attempts = 0
        self.position_value_quote = Decimal("0")
        self.target_price = Decimal("0")
        self.trigger_above = True
        self.balance_update_timeout_sec = 10
        self.balance_refresh_timeout_sec = 30


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "price_unavailable"


def _estimate_position_value(lp, price):
    return (lp.base_amount + lp.base_fee) * price + (lp.quote_amount + lp.quote_fee)


def _make_lp_view(
    state=None,
    base_amount=Decimal("0"),
    quote_amount=Decimal("0"),
    done=False,
    position=True,
):
    return LPView(
        executor_id="lp1",
        is_active=True,
        is_done=done,
        close_type=None,
        state=state,
        position_address="addr" if position else None,
        base_amount=base_amount,
        quote_amount=quote_amount,
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("0.01"),
        upper_price=Decimal("10"),
        out_of_range_since=None,
    )


def _make_snapshot(
    now: float,
    price: Decimal | None,
    lp_view: LPView | None,
    wallet_base=Decimal("0"),
    wallet_quote=Decimal("0"),
):
    lp = {}
    active_lp = []
    if lp_view is not None:
        lp[lp_view.executor_id] = lp_view
        active_lp = [lp_view]
    return Snapshot(
        now=now,
        current_price=price,
        balance_fresh=True,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
        lp=lp,
        swaps={},
        active_lp=active_lp,
        active_swaps=[],
    )


def _make_fsm(config: DummyConfig):
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    return CLMMFSM(
        config=config,
        action_factory=None,
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=engine,
        exit_policy=ExitPolicy(config=config),
    )


def test_risk_equity_caps_wallet_excess():
    config = DummyConfig()
    config.position_value_quote = Decimal("30")
    fsm = _make_fsm(config)
    lp_view = _make_lp_view(base_amount=Decimal("400"))
    snapshot = _make_snapshot(
        now=0,
        price=Decimal("0.05"),
        lp_view=lp_view,
        wallet_base=Decimal("1000"),
    )

    equity = fsm._compute_risk_equity_value(snapshot, lp_view, Decimal("0.05"), None)

    assert equity == Decimal("30")


def test_risk_equity_ignores_wallet_excess():
    config = DummyConfig()
    config.position_value_quote = Decimal("30")
    fsm = _make_fsm(config)
    lp_view = _make_lp_view(base_amount=Decimal("400"))
    snapshot = _make_snapshot(
        now=0,
        price=Decimal("0.05"),
        lp_view=lp_view,
        wallet_base=Decimal("5000"),
    )

    equity = fsm._compute_risk_equity_value(snapshot, lp_view, Decimal("0.05"), None)

    assert equity == Decimal("30")


def test_stoploss_triggers_when_equity_below_threshold():
    config = DummyConfig()
    config.stop_loss_pnl_pct = Decimal("0.1")
    fsm = _make_fsm(config)
    lp_view = _make_lp_view(state=LPPositionStates.IN_RANGE.value, base_amount=Decimal("400"))
    snapshot = _make_snapshot(
        now=100,
        price=Decimal("0.018"),
        lp_view=lp_view,
        wallet_base=Decimal("1000"),
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE
    ctx.anchor_value_quote = Decimal("30")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "stop_loss_triggered"
    assert ctx.state.value == "STOPLOSS_STOP"
    assert decision.actions and isinstance(decision.actions[0], StopExecutorAction)


def test_take_profit_triggers_when_equity_above_threshold():
    config = DummyConfig()
    config.take_profit_pnl_pct = Decimal("0.2")
    fsm = _make_fsm(config)
    lp_view = _make_lp_view(state=LPPositionStates.IN_RANGE.value, base_amount=Decimal("400"))
    snapshot = _make_snapshot(
        now=200,
        price=Decimal("0.09"),
        lp_view=lp_view,
        wallet_base=Decimal("0"),
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE
    ctx.anchor_value_quote = Decimal("30")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "take_profit"
    assert ctx.state.value == "TAKE_PROFIT_STOP"
    assert decision.actions and isinstance(decision.actions[0], StopExecutorAction)


def test_take_profit_not_triggered_by_wallet_excess():
    config = DummyConfig()
    config.take_profit_pnl_pct = Decimal("0.2")
    fsm = _make_fsm(config)
    lp_view = _make_lp_view(state=LPPositionStates.IN_RANGE.value, base_amount=Decimal("400"))
    snapshot = _make_snapshot(
        now=300,
        price=Decimal("0.05"),
        lp_view=lp_view,
        wallet_base=Decimal("1000"),
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE
    ctx.anchor_value_quote = Decimal("30")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "active"
    assert ctx.state.value == "ACTIVE"


def test_stoploss_without_lp_transitions_to_swap():
    config = DummyConfig()
    config.stop_loss_pnl_pct = Decimal("0.1")
    fsm = _make_fsm(config)
    snapshot = _make_snapshot(
        now=400,
        price=Decimal("0.018"),
        lp_view=None,
        wallet_base=Decimal("1000"),
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.IDLE
    ctx.anchor_value_quote = Decimal("30")

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "stop_loss_idle"
    assert ctx.state.value == "STOPLOSS_SWAP"


def test_price_unavailable_blocks_actions():
    config = DummyConfig()
    fsm = _make_fsm(config)
    lp_view = _make_lp_view(state=LPPositionStates.IN_RANGE.value, base_amount=Decimal("400"))
    snapshot = _make_snapshot(
        now=500,
        price=None,
        lp_view=lp_view,
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.ACTIVE

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "price_unavailable"
    assert ctx.state.value == "ACTIVE"


def test_reenter_disabled_blocks_after_take_profit():
    config = DummyConfig()
    config.reenter_enabled = False
    fsm = _make_fsm(config)
    snapshot = _make_snapshot(
        now=600,
        price=Decimal("1"),
        lp_view=None,
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.IDLE
    ctx.last_exit_reason = "take_profit"

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "reenter_disabled"
    assert ctx.state.value == "IDLE"


def test_reenter_disabled_blocks_after_stoploss():
    config = DummyConfig()
    config.reenter_enabled = False
    fsm = _make_fsm(config)
    snapshot = _make_snapshot(
        now=700,
        price=Decimal("1"),
        lp_view=None,
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.IDLE
    ctx.last_exit_reason = "stop_loss"

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "reenter_disabled"
    assert ctx.state.value == "IDLE"


def test_reenter_disabled_allows_initial_entry_attempt():
    config = DummyConfig()
    config.reenter_enabled = False
    fsm = _make_fsm(config)
    snapshot = _make_snapshot(
        now=800,
        price=Decimal("1"),
        lp_view=None,
    )
    ctx = ControllerContext()
    ctx.state = ctx.state.IDLE
    ctx.last_exit_reason = None

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "entry_unavailable"
