import os
import sys
import types
from decimal import Decimal


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates

from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
from bots.controllers.generic.clmm_lp_domain.components import ControllerContext, ControllerState, LPView, Snapshot
from bots.controllers.generic.clmm_lp_domain.exit_policy import ExitPolicy
from bots.controllers.generic.clmm_lp_domain.rebalance_engine import RebalanceEngine


class DummyConfig:
    def __init__(self):
        self.id = "test"
        self.manual_kill_switch = False
        self.rebalance_enabled = False
        self.rebalance_seconds = 60
        self.hysteresis_pct = Decimal("0")
        self.cooldown_seconds = 0
        self.max_rebalances_per_hour = 0
        self.rebalance_open_timeout_sec = 300

        self.exit_full_liquidation = False
        self.exit_swap_slippage_pct = Decimal("0.01")
        self.max_exit_swap_attempts = 3

        self.stop_loss_pnl_pct = Decimal("0")
        self.take_profit_pnl_pct = Decimal("0")
        self.stop_loss_pause_sec = 1800
        self.reenter_enabled = True

        self.position_value_quote = Decimal("0")
        self.target_price = Decimal("0")
        self.trigger_above = True

        self.balance_update_timeout_sec = 2
        self.balance_refresh_timeout_sec = 5


class DummyActionFactory:
    def build_swap_action(self, **_kwargs):
        return types.SimpleNamespace(executor_config=types.SimpleNamespace(id="swap1"))


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "not_used"


def _estimate_position_value(lp: LPView, current_price: Decimal) -> Decimal:
    return (abs(lp.base_amount) + abs(lp.base_fee)) * current_price + (abs(lp.quote_amount) + abs(lp.quote_fee))


def _make_snapshot(*, now: float, price: Decimal, lp_view: LPView) -> Snapshot:
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


def test_fsm_triggers_stoploss_in_active_and_transitions_to_stoploss_stop():
    config = DummyConfig()
    config.stop_loss_pnl_pct = Decimal("0.10")  # 10%
    fsm = CLMMFSM(
        config=config,
        action_factory=DummyActionFactory(),
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=RebalanceEngine(config=config, estimate_position_value=_estimate_position_value),
        exit_policy=ExitPolicy(config=config),
    )

    ctx = ControllerContext()
    ctx.state = ControllerState.ACTIVE
    ctx.anchor_value_quote = Decimal("100")

    lp_view = LPView(
        executor_id="lp1",
        is_active=True,
        is_done=False,
        close_type=None,
        state=LPPositionStates.IN_RANGE.value,
        position_address="0xabc",
        base_amount=Decimal("0"),
        quote_amount=Decimal("89"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("1"),
        upper_price=Decimal("2"),
        out_of_range_since=None,
    )
    snapshot = _make_snapshot(now=1000, price=Decimal("1"), lp_view=lp_view)

    decision = fsm.step(snapshot, ctx)
    assert decision.reason == "stop_loss_triggered"
    assert ctx.state == ControllerState.STOPLOSS_STOP
    assert ctx.last_exit_reason == "stop_loss"
    assert ctx.pending_close_lp_id == "lp1"


def test_fsm_triggers_take_profit_in_active_and_transitions_to_take_profit_stop():
    config = DummyConfig()
    config.take_profit_pnl_pct = Decimal("0.10")  # 10%
    fsm = CLMMFSM(
        config=config,
        action_factory=DummyActionFactory(),
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=RebalanceEngine(config=config, estimate_position_value=_estimate_position_value),
        exit_policy=ExitPolicy(config=config),
    )

    ctx = ControllerContext()
    ctx.state = ControllerState.ACTIVE
    ctx.anchor_value_quote = Decimal("100")

    lp_view = LPView(
        executor_id="lp1",
        is_active=True,
        is_done=False,
        close_type=None,
        state=LPPositionStates.IN_RANGE.value,
        position_address="0xabc",
        base_amount=Decimal("0"),
        quote_amount=Decimal("111"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("1"),
        upper_price=Decimal("2"),
        out_of_range_since=None,
    )
    snapshot = _make_snapshot(now=1000, price=Decimal("1"), lp_view=lp_view)

    decision = fsm.step(snapshot, ctx)
    assert decision.reason == "take_profit"
    assert ctx.state == ControllerState.TAKE_PROFIT_STOP
    assert ctx.last_exit_reason == "take_profit"
    assert ctx.pending_close_lp_id == "lp1"
    assert ctx.pending_realized_anchor == Decimal("100")

