import os
import sys
import types
from decimal import Decimal

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType

for module_name in (
    "bots.controllers.generic.clmm_lp_domain.components",
    "bots.controllers.generic.clmm_lp_domain.clmm_fsm",
    "bots.controllers.generic.clmm_lp_domain.rebalance_engine",
    "bots.controllers.generic.clmm_lp_domain.exit_policy",
):
    sys.modules.pop(module_name, None)
from bots.controllers.generic.clmm_lp_domain.components import (
    ControllerContext,
    ControllerState,
    LPView,
    OpenProposal,
    Snapshot,
    SwapPurpose,
    SwapView,
)
from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
from bots.controllers.generic.clmm_lp_domain.exit_policy import ExitPolicy
from bots.controllers.generic.clmm_lp_domain.rebalance_engine import RebalanceEngine


class DummyConfig:
    def __init__(self):
        self.id = "test"
        self.manual_kill_switch = False
        self.rebalance_enabled = True
        self.rebalance_seconds = 5
        self.hysteresis_pct = Decimal("0")
        self.cooldown_seconds = 0
        self.max_rebalances_per_hour = 10
        self.rebalance_open_timeout_sec = 30
        self.exit_full_liquidation = True
        self.exit_swap_slippage_pct = Decimal("0")
        self.max_exit_swap_attempts = 3
        self.stop_loss_pnl_pct = Decimal("0")
        self.take_profit_pnl_pct = Decimal("0.2")
        self.stop_loss_pause_sec = 0
        self.reenter_enabled = True
        self.position_value_quote = Decimal("0")
        self.target_price = Decimal("0")
        self.trigger_above = True
        self.balance_update_timeout_sec = 2
        self.balance_refresh_timeout_sec = 5


class DummyActionFactory:
    def __init__(self):
        self._open_count = 0
        self._swap_count = 0

    def build_open_lp_action(self, _proposal: OpenProposal, _now: float):
        self._open_count += 1
        return types.SimpleNamespace(
            executor_config=types.SimpleNamespace(id=f"lp{self._open_count}")
        )

    def build_swap_action(self, **_kwargs):
        self._swap_count += 1
        return types.SimpleNamespace(
            executor_config=types.SimpleNamespace(id=f"swap{self._swap_count}")
        )


def _estimate_position_value(lp: LPView, price: Decimal) -> Decimal:
    return (lp.base_amount + lp.base_fee) * price + (lp.quote_amount + lp.quote_fee)


def _build_open_proposal(current_price, wallet_base, wallet_quote, _anchor):
    if current_price is None or current_price <= 0:
        return None, "price_unavailable"
    lower = current_price * Decimal("0.7")
    upper = current_price * Decimal("1.3")
    proposal = OpenProposal(
        lower=lower,
        upper=upper,
        open_base=wallet_base,
        open_quote=wallet_quote,
        target_base=wallet_base,
        target_quote=wallet_quote,
    )
    return proposal, None


def _make_lp_view(
    executor_id: str,
    *,
    state: str,
    lower: Decimal,
    upper: Decimal,
    base_amount: Decimal,
    quote_amount: Decimal = Decimal("0"),
    is_active: bool = True,
    done: bool = False,
    position: bool = True,
):
    return LPView(
        executor_id=executor_id,
        is_active=is_active,
        is_done=done,
        close_type=None,
        state=state,
        position_address="addr" if position else None,
        base_amount=base_amount,
        quote_amount=quote_amount,
        base_fee=Decimal("0"),
        quote_fee=Decimal("0"),
        lower_price=lower,
        upper_price=upper,
        out_of_range_since=None,
    )


def _make_snapshot(
    now: float,
    price: Decimal,
    *,
    lp_views=None,
    swaps=None,
    wallet_base: Decimal = Decimal("0"),
    wallet_quote: Decimal = Decimal("0"),
    balance_fresh: bool = True,
    balance_update_ts: float | None = None,
):
    lp_views = lp_views or []
    swaps = swaps or []
    lp_map = {lp.executor_id: lp for lp in lp_views}
    swap_map = {swap.executor_id: swap for swap in swaps}
    if balance_update_ts is None:
        balance_update_ts = float(now) if balance_fresh else 0.0
    return Snapshot(
        now=now,
        current_price=price,
        balance_fresh=balance_fresh,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
        lp=lp_map,
        swaps=swap_map,
        active_lp=[lp for lp in lp_views if lp.is_active],
        active_swaps=[swap for swap in swaps if swap.is_active],
        balance_update_ts=balance_update_ts,
    )


def test_fsm_flow_rebalance_then_exit_swap():
    config = DummyConfig()
    action_factory = DummyActionFactory()
    engine = RebalanceEngine(config=config, estimate_position_value=_estimate_position_value)
    fsm = CLMMFSM(
        config=config,
        action_factory=action_factory,
        build_open_proposal=_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=engine,
        exit_policy=ExitPolicy(config=config),
    )
    ctx = ControllerContext()

    def _read_decimal_env(name: str, default: str) -> Decimal:
        raw = os.getenv(name, default)
        try:
            return Decimal(str(raw))
        except Exception:
            return Decimal(default)

    p0 = _read_decimal_env("FSM_P0", "1")
    range_pct = _read_decimal_env("FSM_RANGE_PCT", "0.1")
    out_pct = _read_decimal_env("FSM_OUT_PCT", "0.02")
    tp_move_pct = _read_decimal_env("FSM_TP_MOVE_PCT", "0.05")
    if p0 <= 0:
        p0 = Decimal("1")
    if range_pct <= 0:
        range_pct = Decimal("0.1")
    if out_pct <= 0:
        out_pct = Decimal("0.02")
    if tp_move_pct <= 0:
        tp_move_pct = Decimal("0.05")
    wallet_base = Decimal("100")
    lower = p0 * (Decimal("1") - range_pct)
    upper = p0 * (Decimal("1") + range_pct)
    price_out = upper * (Decimal("1") + out_pct)
    price_tp = price_out * (Decimal("1") + tp_move_pct)
    out_pct_total = (price_out / p0) - Decimal("1")
    tp_pct_total = (price_tp / p0) - Decimal("1")
    if tp_pct_total > out_pct_total:
        config.take_profit_pnl_pct = (out_pct_total + tp_pct_total) / 2

    snapshot = _make_snapshot(now=0, price=p0, wallet_base=wallet_base)
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.ENTRY_OPEN
    assert ctx.pending_open_lp_id == "lp1"

    lp1 = _make_lp_view(
        "lp1",
        state=LPPositionStates.IN_RANGE.value,
        lower=lower,
        upper=upper,
        base_amount=wallet_base,
    )
    snapshot = _make_snapshot(now=1, price=p0, lp_views=[lp1])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.ACTIVE

    snapshot = _make_snapshot(now=2, price=price_out, lp_views=[lp1])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.ACTIVE
    assert ctx.out_of_range_since == 2

    snapshot = _make_snapshot(
        now=2 + config.rebalance_seconds + 1,
        price=price_out,
        lp_views=[lp1],
    )
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.REBALANCE_STOP

    lp1_closed = _make_lp_view(
        "lp1",
        state=LPPositionStates.COMPLETE.value,
        lower=lower,
        upper=upper,
        base_amount=Decimal("0"),
        quote_amount=Decimal("0"),
        is_active=False,
        done=True,
        position=False,
    )
    snapshot = _make_snapshot(now=9, price=price_out, lp_views=[lp1_closed])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.REBALANCE_OPEN

    snapshot = _make_snapshot(now=10, price=price_out)
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.REBALANCE_OPEN
    assert ctx.pending_open_lp_id == "lp2"

    lower2 = price_out * Decimal("0.7")
    upper2 = price_out * Decimal("1.3")
    lp2 = _make_lp_view(
        "lp2",
        state=LPPositionStates.IN_RANGE.value,
        lower=lower2,
        upper=upper2,
        base_amount=wallet_base,
    )
    snapshot = _make_snapshot(now=11, price=price_out, lp_views=[lp2])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.ACTIVE

    snapshot = _make_snapshot(now=12, price=price_tp, lp_views=[lp2])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.TAKE_PROFIT_STOP

    lp2_closed = _make_lp_view(
        "lp2",
        state=LPPositionStates.COMPLETE.value,
        lower=lower2,
        upper=upper2,
        base_amount=Decimal("0"),
        quote_amount=Decimal("0"),
        is_active=False,
        done=True,
        position=False,
    )
    snapshot = _make_snapshot(now=13, price=price_tp, lp_views=[lp2_closed])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.EXIT_SWAP

    snapshot = _make_snapshot(
        now=14,
        price=price_tp,
        wallet_base=Decimal("50"),
        wallet_quote=Decimal("0"),
    )
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.EXIT_SWAP
    assert ctx.pending_swap_id == "swap1"

    swap_done = SwapView(
        executor_id="swap1",
        is_active=False,
        is_done=True,
        close_type=CloseType.COMPLETED,
        timestamp=14.5,
        level_id=SwapPurpose.EXIT_LIQUIDATION.value,
        purpose=SwapPurpose.EXIT_LIQUIDATION,
        amount=Decimal("50"),
    )
    snapshot = _make_snapshot(now=15, price=price_tp, swaps=[swap_done])
    fsm.step(snapshot, ctx)
    assert ctx.state == ControllerState.IDLE
