import os
import sys
import types
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

# ---- Stubs for hummingbot dependencies ----
common_module = types.ModuleType("hummingbot.core.data_type.common")


class TradeType(Enum):
    BUY = 1
    SELL = 2


common_module.TradeType = TradeType
sys.modules.setdefault("hummingbot.core.data_type.common", common_module)

executors_module = types.ModuleType("hummingbot.strategy_v2.models.executors")


class CloseType(Enum):
    FAILED = 8
    COMPLETED = 9


executors_module.CloseType = CloseType
sys.modules.setdefault("hummingbot.strategy_v2.models.executors", executors_module)

executor_actions_module = types.ModuleType("hummingbot.strategy_v2.models.executor_actions")


@dataclass(frozen=True)
class ExecutorAction:
    controller_id: str = ""


@dataclass(frozen=True)
class StopExecutorAction(ExecutorAction):
    executor_id: str = ""


executor_actions_module.ExecutorAction = ExecutorAction
executor_actions_module.StopExecutorAction = StopExecutorAction
sys.modules["hummingbot.strategy_v2.models.executor_actions"] = executor_actions_module

lp_states_module = types.ModuleType("hummingbot.strategy_v2.executors.lp_position_executor.data_types")


class LPPositionStates(Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    OPENING = "OPENING"
    IN_RANGE = "IN_RANGE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"
    RETRIES_EXCEEDED = "RETRIES_EXCEEDED"


lp_states_module.LPPositionStates = LPPositionStates
sys.modules["hummingbot.strategy_v2.executors.lp_position_executor.data_types"] = lp_states_module

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
        self.reenter_enabled = True
        self.max_inventory_swap_attempts = 0
        self.inventory_drift_tolerance_pct = Decimal("0")
        self.normalization_cooldown_sec = 0
        self.normalization_min_value_pct = Decimal("0")
        self.normalization_strict = False
        self.max_stoploss_liquidation_attempts = 0


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
