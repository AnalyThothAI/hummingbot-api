import os
import sys
import types
from decimal import Decimal


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


for module_name in (
    "bots.controllers.generic.clmm_lp_domain.components",
    "bots.controllers.generic.clmm_lp_domain.clmm_fsm",
    "bots.controllers.generic.clmm_lp_domain.rebalance_engine",
    "bots.controllers.generic.clmm_lp_domain.exit_policy",
):
    sys.modules.pop(module_name, None)

from bots.controllers.generic.clmm_lp_domain.components import ControllerContext, ControllerState, Snapshot
from bots.controllers.generic.clmm_lp_domain.clmm_fsm import CLMMFSM
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
        self.exit_full_liquidation = True
        self.exit_swap_slippage_pct = Decimal("0")
        self.max_exit_swap_attempts = 3
        self.stop_loss_pnl_pct = Decimal("0")
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
        return types.SimpleNamespace(
            executor_config=types.SimpleNamespace(id="swap1")
        )

class CapturingActionFactory(DummyActionFactory):
    def __init__(self):
        self.last_kwargs = None

    def build_swap_action(self, **kwargs):
        self.last_kwargs = kwargs
        return super().build_swap_action(**kwargs)


def _dummy_build_open_proposal(*_args, **_kwargs):
    return None, "price_unavailable"


def _estimate_position_value(_lp, _price):
    return Decimal("0")


def _make_snapshot(*, now: float, balance_fresh: bool, wallet_base: Decimal, wallet_quote: Decimal) -> Snapshot:
    return Snapshot(
        now=now,
        current_price=Decimal("1"),
        balance_fresh=balance_fresh,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
        lp={},
        swaps={},
        active_lp=[],
        active_swaps=[],
    )


def test_exit_swap_waits_multiple_balance_refresh_attempts_when_stale():
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
    ctx.state = ControllerState.EXIT_SWAP
    ctx.last_exit_reason = "stop_loss"

    snapshot = _make_snapshot(now=0, balance_fresh=False, wallet_base=Decimal("0"), wallet_quote=Decimal("10"))
    decision1 = fsm.step(snapshot, ctx)
    assert decision1.reason == "exit_refresh_balance"
    assert ctx.exit_balance_refresh_attempts == 1
    assert ctx.state == ControllerState.EXIT_SWAP

    decision2 = fsm.step(snapshot, ctx)
    assert decision2.reason == "exit_refresh_balance"
    assert ctx.exit_balance_refresh_attempts == 2
    assert ctx.state == ControllerState.EXIT_SWAP


def test_exit_swap_keeps_min_native_balance_when_base_is_native():
    config = DummyConfig()
    config.trading_pair = "SOL-USDC"
    config.native_token_symbol = "SOL"
    config.min_native_balance = Decimal("0.1")
    action_factory = CapturingActionFactory()
    fsm = CLMMFSM(
        config=config,
        action_factory=action_factory,
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=RebalanceEngine(config=config, estimate_position_value=_estimate_position_value),
        exit_policy=ExitPolicy(config=config),
    )
    ctx = ControllerContext()
    ctx.state = ControllerState.EXIT_SWAP
    ctx.last_exit_reason = "stop_loss"

    snapshot = _make_snapshot(now=0, balance_fresh=True, wallet_base=Decimal("1.0"), wallet_quote=Decimal("0"))
    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "exit_swap"
    assert action_factory.last_kwargs is not None
    assert action_factory.last_kwargs["amount"] == Decimal("0.9")


def test_exit_swap_skips_when_only_min_native_balance_remains():
    config = DummyConfig()
    config.trading_pair = "SOL-USDC"
    config.native_token_symbol = "SOL"
    config.min_native_balance = Decimal("0.1")
    action_factory = CapturingActionFactory()
    fsm = CLMMFSM(
        config=config,
        action_factory=action_factory,
        build_open_proposal=_dummy_build_open_proposal,
        estimate_position_value=_estimate_position_value,
        rebalance_engine=RebalanceEngine(config=config, estimate_position_value=_estimate_position_value),
        exit_policy=ExitPolicy(config=config),
    )
    ctx = ControllerContext()
    ctx.state = ControllerState.EXIT_SWAP
    ctx.last_exit_reason = "stop_loss"

    snapshot = _make_snapshot(now=0, balance_fresh=True, wallet_base=Decimal("0.05"), wallet_quote=Decimal("0"))
    decision = fsm.step(snapshot, ctx)

    assert decision.reason == "exit_no_base"
    assert action_factory.last_kwargs is None
