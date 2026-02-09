import types
from decimal import Decimal

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType

from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
from bots.controllers.generic.clmm_lp_domain.components import (
    ControllerContext,
    ControllerState,
    LPView,
    Snapshot,
    SwapPurpose,
    SwapView,
)
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

        self.exit_full_liquidation = True
        self.exit_swap_slippage_pct = Decimal("0")
        self.max_exit_swap_attempts = 3

        self.stop_loss_pnl_pct = Decimal("0.10")  # 10%
        self.take_profit_pnl_pct = Decimal("0")
        self.stop_loss_pause_sec = 0
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


def _make_snapshot(
    *,
    now: float,
    price: Decimal,
    lp_view: LPView | None = None,
    swaps: list[SwapView] | None = None,
    wallet_base: Decimal = Decimal("0"),
    wallet_quote: Decimal = Decimal("0"),
    balance_fresh: bool = True,
    balance_update_ts: float = 0.0,
) -> Snapshot:
    lp = {lp_view.executor_id: lp_view} if lp_view is not None else {}
    swaps = swaps or []
    swaps_map = {s.executor_id: s for s in swaps}
    active_lp = [lp_view] if lp_view is not None and lp_view.is_active else []
    active_swaps = [s for s in swaps if s.is_active]
    return Snapshot(
        now=now,
        current_price=price,
        balance_fresh=balance_fresh,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
        lp=lp,
        swaps=swaps_map,
        active_lp=active_lp,
        active_swaps=active_swaps,
        balance_update_ts=balance_update_ts,
    )


def test_stoploss_close_then_exit_swap_waits_for_post_close_balance_update():
    config = DummyConfig()
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

    lp_open = LPView(
        executor_id="lp1",
        is_active=True,
        is_done=False,
        close_type=None,
        state=LPPositionStates.IN_RANGE.value,
        position_address="0xabc",
        base_amount=Decimal("0"),
        quote_amount=Decimal("80"),  # triggers stoploss vs anchor=100 at 10%
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("1"),
        upper_price=Decimal("2"),
        out_of_range_since=None,
    )
    snap1 = _make_snapshot(now=1000.0, price=Decimal("1"), lp_view=lp_open, balance_update_ts=1000.0)
    d1 = fsm.step(snap1, ctx)
    assert d1.reason == "stop_loss_triggered"
    assert ctx.state == ControllerState.STOPLOSS_STOP

    lp_closed = LPView(
        executor_id="lp1",
        is_active=False,
        is_done=True,
        close_type=CloseType.COMPLETED,
        state=LPPositionStates.COMPLETE.value,
        position_address=None,
        base_amount=Decimal("0"),
        quote_amount=Decimal("0"),
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=Decimal("1"),
        upper_price=Decimal("2"),
        out_of_range_since=None,
    )
    snap2 = _make_snapshot(now=1001.0, price=Decimal("1"), lp_view=lp_closed, balance_update_ts=1000.0)
    fsm.step(snap2, ctx)
    assert ctx.state == ControllerState.EXIT_SWAP

    # Still seeing the pre-close balance snapshot (fresh by ttl but not updated since EXIT_SWAP entry).
    snap3 = _make_snapshot(
        now=1001.1,
        price=Decimal("1"),
        wallet_base=Decimal("0"),
        wallet_quote=Decimal("10"),
        balance_fresh=True,
        balance_update_ts=1000.0,
    )
    d3 = fsm.step(snap3, ctx)
    assert d3.reason == "exit_refresh_balance"
    assert ctx.state == ControllerState.EXIT_SWAP
    assert ctx.pending_swap_id is None

    # Post-close balance update arrives; liquidation swap should be created.
    snap4 = _make_snapshot(
        now=1002.0,
        price=Decimal("1"),
        wallet_base=Decimal("10"),
        wallet_quote=Decimal("0"),
        balance_fresh=True,
        balance_update_ts=1002.0,
    )
    d4 = fsm.step(snap4, ctx)
    assert d4.reason == "exit_swap"
    assert ctx.pending_swap_id == "swap1"

    swap_done = SwapView(
        executor_id="swap1",
        is_active=False,
        is_done=True,
        close_type=CloseType.COMPLETED,
        timestamp=1002.5,
        level_id=SwapPurpose.EXIT_LIQUIDATION.value,
        purpose=SwapPurpose.EXIT_LIQUIDATION,
        amount=Decimal("10"),
    )
    snap5 = _make_snapshot(now=1003.0, price=Decimal("1"), swaps=[swap_done], balance_update_ts=1003.0)
    fsm.step(snap5, ctx)
    assert ctx.state == ControllerState.COOLDOWN

