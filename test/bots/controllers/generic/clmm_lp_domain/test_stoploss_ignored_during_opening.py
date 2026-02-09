import os
import sys
from decimal import Decimal

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates

from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
from bots.controllers.generic.clmm_lp_domain.components import ControllerContext, ControllerState, LPView, Snapshot
from bots.controllers.generic.clmm_lp_domain.exit_policy import ExitPolicy
from bots.controllers.generic.clmm_lp_domain.rebalance_engine import RebalanceEngine


class DummyConfig:
    def __init__(self) -> None:
        self.id = "test"
        self.manual_kill_switch = False
        self.rebalance_enabled = True
        self.rebalance_seconds = 60
        self.hysteresis_pct = Decimal("0")
        self.cooldown_seconds = 0
        self.max_rebalances_per_hour = 10
        self.rebalance_open_timeout_sec = 300
        self.exit_full_liquidation = False
        self.exit_swap_slippage_pct = Decimal("0")
        self.max_exit_swap_attempts = 0
        self.stop_loss_pnl_pct = Decimal("0.1")  # 10%
        self.take_profit_pnl_pct = Decimal("0")
        self.stop_loss_pause_sec = 1800
        self.reenter_enabled = True
        self.position_value_quote = Decimal("0")
        self.target_price = Decimal("0")
        self.trigger_above = True
        self.balance_update_timeout_sec = 2
        self.balance_refresh_timeout_sec = 5


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "not_used"


def _estimate_position_value(lp: LPView, current_price: Decimal) -> Decimal:
    return (abs(lp.base_amount) + abs(lp.base_fee)) * current_price + (abs(lp.quote_amount) + abs(lp.quote_fee))


def test_stoploss_does_not_trigger_while_lp_opening_in_rebalance_open():
    config = DummyConfig()
    fsm = CLMMFSM(
        config=config,
        action_factory=None,
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=RebalanceEngine(config=config, estimate_position_value=_estimate_position_value),
        exit_policy=ExitPolicy(config=config),
    )

    ctx = ControllerContext()
    ctx.state = ControllerState.REBALANCE_OPEN
    ctx.anchor_value_quote = Decimal("100")
    ctx.pending_open_lp_id = "lp2"
    ctx.state_since_ts = 1000

    lp_view = LPView(
        executor_id="lp2",
        is_active=True,
        is_done=False,
        close_type=None,
        state=LPPositionStates.OPENING.value,
        position_address=None,
        base_amount=Decimal("0"),
        quote_amount=Decimal("0"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("1"),
        upper_price=Decimal("2"),
        out_of_range_since=None,
    )
    snapshot = Snapshot(
        now=1001,
        current_price=Decimal("1"),
        balance_fresh=True,
        wallet_base=Decimal("0"),
        wallet_quote=Decimal("0"),
        lp={lp_view.executor_id: lp_view},
        swaps={},
        active_lp=[lp_view],
        active_swaps=[],
    )

    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "open_in_progress"
    assert ctx.state == ControllerState.REBALANCE_OPEN
