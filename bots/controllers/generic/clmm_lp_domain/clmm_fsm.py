from decimal import Decimal
from typing import Callable, List, Optional, Tuple

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction

from .components import (
    ControllerContext,
    ControllerState,
    Decision,
    LPView,
    OpenProposal,
    Snapshot,
    SwapPurpose,
    SwapView,
)
from .exit_policy import ExitPolicy
from .rebalance_engine import RebalanceEngine

BuildOpenProposal = Callable[
    [Optional[Decimal], Decimal, Decimal, Optional[Decimal]],
    Tuple[Optional[OpenProposal], Optional[str]],
]
EstimatePositionValue = Callable[[LPView, Decimal], Decimal]


class CLMMFSM:
    _pending_swap_grace_sec = 30.0
    def __init__(
        self,
        *,
        config,
        action_factory,
        build_open_proposal: BuildOpenProposal,
        estimate_position_value: EstimatePositionValue,
        rebalance_engine: RebalanceEngine,
        exit_policy: ExitPolicy,
    ) -> None:
        self._config = config
        self._action_factory = action_factory
        self._build_open_proposal = build_open_proposal
        self._estimate_position_value = estimate_position_value
        self._rebalance_engine = rebalance_engine
        self._exit_policy = exit_policy

    def step(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        if ctx.state_since_ts <= 0:
            ctx.state_since_ts = now

        decision = self._guard_concurrency(snapshot)
        if decision is not None:
            self._record_decision(ctx, decision.reason)
            return decision

        state = ctx.state
        if self._config.manual_kill_switch and state not in {
            ControllerState.STOPLOSS_STOP,
            ControllerState.EXIT_SWAP,
        }:
            decision = self._force_manual_stop(snapshot, ctx)
            self._record_decision(ctx, decision.reason)
            return decision
        decision = self._guard_price(snapshot, ctx)
        if decision is not None:
            return decision
        if state == ControllerState.IDLE:
            return self._handle_idle(snapshot, ctx)
        if state == ControllerState.ENTRY_OPEN:
            return self._handle_entry_open(snapshot, ctx)
        if state == ControllerState.ACTIVE:
            return self._handle_active(snapshot, ctx)
        if state == ControllerState.REBALANCE_STOP:
            return self._handle_rebalance_stop(snapshot, ctx)
        if state == ControllerState.REBALANCE_OPEN:
            return self._handle_rebalance_open(snapshot, ctx)
        if state == ControllerState.TAKE_PROFIT_STOP:
            return self._handle_take_profit_stop(snapshot, ctx)
        if state == ControllerState.STOPLOSS_STOP:
            return self._handle_stoploss_stop(snapshot, ctx)
        if state == ControllerState.EXIT_SWAP:
            return self._handle_exit_swap(snapshot, ctx)
        if state == ControllerState.COOLDOWN:
            return self._handle_cooldown(snapshot, ctx)
        ctx.state = ControllerState.IDLE
        ctx.state_since_ts = now
        return Decision(reason="state_reset")

    def _guard_concurrency(self, snapshot: Snapshot) -> Optional[Decision]:
        if len(snapshot.active_swaps) > 1:
            keep = self._select_swap_to_keep(snapshot.active_swaps)
            if keep is None:
                return None
            actions = [
                StopExecutorAction(controller_id=self._config.id, executor_id=swap.executor_id)
                for swap in snapshot.active_swaps
                if swap.executor_id != keep.executor_id
            ]
            if actions:
                return Decision(actions=actions, reason="swap_concurrency_guard")
        if len(snapshot.active_lp) > 1:
            keep = min(snapshot.active_lp, key=lambda lp: lp.executor_id)
            actions = [
                StopExecutorAction(controller_id=self._config.id, executor_id=lp.executor_id)
                for lp in snapshot.active_lp
                if lp.executor_id != keep.executor_id
            ]
            if actions:
                return Decision(actions=actions, reason="lp_concurrency_guard")
        return None

    def _handle_idle(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        ctx.pending_close_lp_id = None
        ctx.pending_open_lp_id = None
        ctx.pending_swap_id = None
        ctx.pending_swap_since_ts = 0.0
        ctx.pending_swap_purpose = None
        lp_view = self._select_lp(snapshot, ctx)
        if ctx.pending_realized_anchor is not None and (lp_view is None or self._is_lp_closed(lp_view)):
            self._record_realized_on_close(snapshot, ctx, lp_view, reason="idle")
        if lp_view is not None:
            if self._is_lp_open(lp_view):
                self._set_anchor_if_ready(snapshot, ctx, lp_view)
                return self._transition(ctx, ControllerState.ACTIVE, now, reason="lp_already_open")
            if self._is_lp_in_transition(lp_view):
                return self._stay(ctx, reason="lp_in_transition")
            if self._is_lp_failed(lp_view):
                return self._stay(ctx, reason="lp_failed")
        if ctx.anchor_value_quote is not None:
            stoploss_decision = self._maybe_stoploss(snapshot, ctx, lp_view, now, reason="stop_loss_idle")
            if stoploss_decision is not None:
                return stoploss_decision
        if not self._can_reenter(ctx):
            return self._stay(ctx, reason="reenter_disabled")
        if not self._is_entry_triggered(snapshot.current_price):
            return self._stay(ctx, reason="idle")
        return self._plan_entry_open(snapshot, ctx)

    def _handle_entry_open(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        lp_view = self._select_lp(snapshot, ctx)
        if lp_view and self._is_lp_open(lp_view):
            self._set_anchor_if_ready(snapshot, ctx, lp_view)
            ctx.pending_open_lp_id = None
            return self._transition(ctx, ControllerState.ACTIVE, now, reason="entry_opened")
        if lp_view and self._is_lp_failed(lp_view):
            ctx.pending_open_lp_id = None
            return self._enter_cooldown(ctx, now, reason="entry_lp_failed")
        if lp_view and self._is_lp_in_transition(lp_view):
            if ctx.pending_open_lp_id and self._open_timeout_exceeded(ctx, now):
                return self._enter_cooldown(ctx, now, reason="entry_open_timeout", actions=self._stop_lp_action(lp_view))
            return self._stay(ctx, reason="open_in_progress")
        if ctx.pending_open_lp_id and self._open_timeout_exceeded(ctx, now):
            return self._enter_cooldown(ctx, now, reason="entry_open_timeout", actions=self._stop_lp_action(lp_view))
        if not self._is_entry_triggered(snapshot.current_price):
            return self._transition(ctx, ControllerState.IDLE, now, reason="entry_not_triggered")
        if snapshot.active_swaps:
            return self._stay(ctx, reason="swap_in_progress")
        if ctx.pending_open_lp_id and (now - ctx.state_since_ts) < self._open_timeout_sec():
            return self._stay(ctx, reason="open_in_progress")
        ctx.pending_open_lp_id = None
        return self._plan_entry_open(snapshot, ctx)

    def _handle_active(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        lp_view = self._select_lp(snapshot, ctx)
        if lp_view is None or not self._is_lp_open(lp_view):
            return self._transition(ctx, ControllerState.IDLE, now, reason="lp_missing")
        if self._is_lp_failed(lp_view):
            return self._transition(ctx, ControllerState.IDLE, now, reason="lp_failed")
        if self._is_lp_in_transition(lp_view):
            return self._stay(ctx, reason="lp_in_transition")
        stoploss_decision = self._maybe_stoploss(snapshot, ctx, lp_view, now, reason="stop_loss_triggered")
        if stoploss_decision is not None:
            return stoploss_decision
        self._set_anchor_if_ready(snapshot, ctx, lp_view)
        self._update_out_of_range_timer(snapshot, ctx, lp_view)
        current_price = self._effective_price(snapshot, lp_view)
        equity = None
        if current_price is not None and current_price > 0:
            equity = self._compute_risk_equity_value(snapshot, lp_view, current_price, ctx.anchor_value_quote)
        signal = self._rebalance_engine.evaluate(snapshot, ctx, lp_view)
        ctx.rebalance_signal_reason = signal.reason
        if signal.should_rebalance:
            self._rebalance_engine.record_rebalance(now, ctx)
            ctx.pending_open_lp_id = None
            ctx.pending_close_lp_id = lp_view.executor_id
            stop_action = StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)
            return self._transition(
                ctx,
                ControllerState.REBALANCE_STOP,
                now,
                reason=signal.reason,
                actions=[stop_action],
            )
        if self._exit_policy.should_take_profit(ctx.anchor_value_quote, equity):
            ctx.last_exit_reason = "take_profit"
            if ctx.pending_realized_anchor is None:
                ctx.pending_realized_anchor = ctx.anchor_value_quote
            ctx.pending_open_lp_id = None
            ctx.pending_close_lp_id = lp_view.executor_id
            stop_action = StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)
            return self._transition(
                ctx,
                ControllerState.TAKE_PROFIT_STOP,
                now,
                reason="take_profit",
                actions=[stop_action],
            )
        return self._stay(ctx, reason="active")

    def _handle_rebalance_stop(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        lp_view = self._select_lp(snapshot, ctx)
        stoploss_decision = self._maybe_stoploss(snapshot, ctx, lp_view, now, reason="stop_loss_rebalance")
        if stoploss_decision is not None:
            return stoploss_decision
        if lp_view is None or self._is_lp_closed(lp_view):
            self._record_realized_on_close(snapshot, ctx, lp_view, reason="rebalance")
            ctx.pending_close_lp_id = None
            return self._transition(ctx, ControllerState.REBALANCE_OPEN, now, reason="rebalance_lp_closed")
        if self._is_lp_in_transition(lp_view):
            return self._stay(ctx, reason="rebalance_stop_in_transition")
        stop_action = StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)
        ctx.pending_close_lp_id = lp_view.executor_id
        return self._stay(ctx, reason="rebalance_stop", actions=[stop_action])

    def _handle_rebalance_open(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        lp_view = self._select_lp(snapshot, ctx)
        stoploss_decision = self._maybe_stoploss(snapshot, ctx, lp_view, now, reason="stop_loss_rebalance")
        if stoploss_decision is not None:
            return stoploss_decision
        if lp_view and self._is_lp_open(lp_view):
            self._set_anchor_if_ready(snapshot, ctx, lp_view)
            ctx.pending_open_lp_id = None
            return self._transition(ctx, ControllerState.ACTIVE, now, reason="rebalance_opened")
        if lp_view and self._is_lp_failed(lp_view):
            ctx.pending_open_lp_id = None
            return self._enter_cooldown(ctx, now, reason="rebalance_lp_failed")
        if lp_view and self._is_lp_in_transition(lp_view):
            if ctx.pending_open_lp_id and self._open_timeout_exceeded(ctx, now):
                return self._enter_cooldown(ctx, now, reason="rebalance_open_timeout", actions=self._stop_lp_action(lp_view))
            return self._stay(ctx, reason="open_in_progress")
        if ctx.pending_open_lp_id and self._open_timeout_exceeded(ctx, now):
            return self._enter_cooldown(ctx, now, reason="rebalance_open_timeout", actions=self._stop_lp_action(lp_view))
        if snapshot.active_swaps:
            return self._stay(ctx, reason="swap_in_progress")
        if ctx.pending_open_lp_id and (now - ctx.state_since_ts) < self._open_timeout_sec():
            return self._stay(ctx, reason="open_in_progress")
        ctx.pending_open_lp_id = None
        plan = self._build_open_plan(snapshot, ctx)
        if plan is None:
            return self._transition(ctx, ControllerState.IDLE, now, reason="rebalance_unavailable")
        open_action = self._action_factory.build_open_lp_action(plan, now)
        if open_action is None:
            return self._stay(ctx, reason="budget_unavailable")
        ctx.pending_open_lp_id = open_action.executor_config.id
        ctx.state_since_ts = now
        return self._stay(ctx, reason="rebalance_open", actions=[open_action])

    def _handle_take_profit_stop(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        lp_view = self._select_lp(snapshot, ctx)
        if lp_view is None or self._is_lp_closed(lp_view):
            self._record_realized_on_close(snapshot, ctx, lp_view, reason="take_profit")
            ctx.pending_close_lp_id = None
            if self._config.exit_full_liquidation:
                return self._transition(ctx, ControllerState.EXIT_SWAP, now, reason="take_profit_exit_swap")
            return self._transition(ctx, ControllerState.IDLE, now, reason="take_profit_closed")
        if self._is_lp_in_transition(lp_view):
            return self._stay(ctx, reason="take_profit_stop_in_transition")
        stop_action = StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)
        ctx.pending_close_lp_id = lp_view.executor_id
        return self._stay(ctx, reason="take_profit_stop", actions=[stop_action])

    def _handle_stoploss_stop(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        lp_view = self._select_lp(snapshot, ctx)
        if lp_view is None or self._is_lp_closed(lp_view):
            self._record_realized_on_close(snapshot, ctx, lp_view, reason="stop_loss")
            ctx.pending_close_lp_id = None
            if self._config.exit_full_liquidation:
                return self._transition(ctx, ControllerState.EXIT_SWAP, now, reason="stoploss_exit_swap")
            return self._transition(ctx, ControllerState.COOLDOWN, now, reason="stoploss_closed")
        if self._is_lp_in_transition(lp_view):
            return self._stay(ctx, reason="stoploss_stop_in_transition")
        stop_action = StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)
        ctx.pending_close_lp_id = lp_view.executor_id
        return self._stay(ctx, reason="stoploss_stop", actions=[stop_action])

    def _handle_exit_swap(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        if not snapshot.balance_fresh and ctx.exit_balance_refresh_attempts < 1:
            self._request_balance_refresh(ctx, now, reason="exit_refresh")
            ctx.exit_balance_refresh_attempts += 1
            return self._stay(ctx, reason="exit_refresh_balance")

        if self._resolve_pending_swap(snapshot, ctx, is_exit=True):
            return self._transition(ctx, self._exit_done_state(ctx), now, reason="exit_swap_done")

        pending_guard = self._guard_pending_swap(snapshot, ctx)
        if pending_guard is not None:
            return pending_guard

        if self._exit_attempts_exhausted(ctx, self._config.max_exit_swap_attempts):
            return self._transition(ctx, self._exit_done_state(ctx), now, reason="exit_swap_failed")

        if any(snapshot.active_swaps):
            return self._stay(ctx, reason="swap_in_progress")

        base_to_sell = snapshot.wallet_base
        if base_to_sell <= 0:
            return self._transition(ctx, self._exit_done_state(ctx), now, reason="exit_no_base")

        swap_action = self._action_factory.build_swap_action(
            level_id=SwapPurpose.EXIT_LIQUIDATION.value,
            now=now,
            side=TradeType.SELL,
            amount=base_to_sell,
            amount_in_is_quote=False,
        )
        if swap_action is None:
            return self._transition(ctx, self._exit_done_state(ctx), now, reason="exit_swap_unavailable")
        ctx.pending_swap_id = swap_action.executor_config.id
        ctx.pending_swap_since_ts = now
        ctx.pending_swap_purpose = SwapPurpose.EXIT_LIQUIDATION
        ctx.last_exit_swap_ts = now
        ctx.exit_swap_attempts += 1
        return Decision(actions=[swap_action], reason="exit_swap")

    def _handle_cooldown(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        if now < ctx.cooldown_until_ts:
            return self._stay(ctx, reason="cooldown")
        if ctx.pending_realized_anchor is not None:
            self._record_realized_on_close(snapshot, ctx, None, reason="cooldown")
        return self._transition(ctx, ControllerState.IDLE, now, reason="cooldown_complete")

    def _enter_cooldown(
        self,
        ctx: ControllerContext,
        now: float,
        *,
        reason: str,
        actions: Optional[list] = None,
    ) -> Decision:
        cooldown_sec = max(0, int(self._config.cooldown_seconds))
        if cooldown_sec <= 0:
            return self._transition(ctx, ControllerState.IDLE, now, reason=reason, actions=actions)
        ctx.cooldown_until_ts = now + cooldown_sec
        return self._transition(ctx, ControllerState.COOLDOWN, now, reason=reason, actions=actions)

    def _open_timeout_exceeded(self, ctx: ControllerContext, now: float) -> bool:
        timeout = self._open_timeout_sec()
        if timeout <= 0 or ctx.state_since_ts <= 0:
            return False
        return (now - ctx.state_since_ts) >= timeout

    def _stop_lp_action(self, lp_view: Optional[LPView]) -> list:
        if lp_view is None:
            return []
        return [StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)]

    def _plan_entry_open(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        plan = self._build_open_plan(snapshot, ctx)
        if plan is None:
            return self._transition(ctx, ControllerState.IDLE, snapshot.now, reason="entry_unavailable")
        open_action = self._action_factory.build_open_lp_action(plan, snapshot.now)
        if open_action is None:
            return self._stay(ctx, reason="budget_unavailable")
        ctx.pending_open_lp_id = open_action.executor_config.id
        ctx.state_since_ts = snapshot.now
        return self._transition(ctx, ControllerState.ENTRY_OPEN, snapshot.now, reason="entry_open", actions=[open_action])

    def _build_open_plan(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[OpenProposal]:
        proposal, _ = self._build_open_proposal(
            snapshot.current_price,
            snapshot.wallet_base,
            snapshot.wallet_quote,
            ctx.anchor_value_quote,
        )
        return proposal

    def _resolve_pending_swap(self, snapshot: Snapshot, ctx: ControllerContext, is_exit: bool = False) -> bool:
        if not ctx.pending_swap_id:
            return False
        swap = snapshot.swaps.get(ctx.pending_swap_id)
        if swap is None:
            swap = self._find_recent_completed_swap(snapshot, ctx, is_exit)
        if swap is None or not swap.is_done:
            return False
        expected_purpose = ctx.pending_swap_purpose
        if expected_purpose is not None and swap.purpose != expected_purpose:
            return False
        ctx.pending_swap_id = None
        ctx.pending_swap_since_ts = 0.0
        ctx.pending_swap_purpose = None
        if swap.close_type != CloseType.COMPLETED:
            return False
        if is_exit:
            ctx.exit_swap_attempts = 0
            ctx.exit_balance_refresh_attempts = 0
        self._request_balance_refresh(ctx, snapshot.now, reason="swap_done")
        return True

    def _exit_attempts_exhausted(self, ctx: ControllerContext, max_attempts: int) -> bool:
        if max_attempts <= 0:
            return False
        return ctx.exit_swap_attempts >= max_attempts

    @staticmethod
    def _exit_done_state(ctx: ControllerContext) -> ControllerState:
        if ctx.last_exit_reason == "stop_loss":
            return ControllerState.COOLDOWN
        return ControllerState.IDLE

    def _open_timeout_sec(self) -> float:
        return float(max(0, self._config.rebalance_open_timeout_sec))

    def _is_entry_triggered(self, current_price: Optional[Decimal]) -> bool:
        if self._config.target_price <= 0:
            return True
        if current_price is None:
            return False
        if self._config.trigger_above:
            return current_price >= self._config.target_price
        return current_price <= self._config.target_price

    def _set_anchor_if_ready(self, snapshot: Snapshot, ctx: ControllerContext, lp_view: Optional[LPView]) -> None:
        if ctx.anchor_value_quote is not None:
            return
        current_price = self._effective_price(snapshot, lp_view)
        if current_price is None or current_price <= 0:
            return
        equity = self._compute_risk_equity_value(snapshot, lp_view, current_price, None)
        if equity is None or equity <= 0:
            return
        ctx.anchor_value_quote = self._anchor_baseline(equity)

    def _maybe_stoploss(
        self,
        snapshot: Snapshot,
        ctx: ControllerContext,
        lp_view: Optional[LPView],
        now: float,
        *,
        reason: str,
    ) -> Optional[Decision]:
        current_price = self._effective_price(snapshot, lp_view)
        if current_price is None or current_price <= 0:
            return None
        equity = self._compute_risk_equity_value(snapshot, lp_view, current_price, ctx.anchor_value_quote)
        if equity is None:
            return None
        if ctx.anchor_value_quote is None:
            ctx.anchor_value_quote = self._anchor_baseline(equity)
        if not self._exit_policy.should_stoploss(ctx.anchor_value_quote, equity):
            return None
        ctx.last_exit_reason = "stop_loss"
        ctx.cooldown_until_ts = now + self._config.stop_loss_pause_sec
        if ctx.pending_realized_anchor is None:
            ctx.pending_realized_anchor = ctx.anchor_value_quote
        if lp_view is None or self._is_lp_closed(lp_view):
            if self._config.exit_full_liquidation:
                return self._transition(
                    ctx,
                    ControllerState.EXIT_SWAP,
                    now,
                    reason=reason,
                )
            return self._transition(
                ctx,
                ControllerState.COOLDOWN,
                now,
                reason=reason,
            )
        ctx.pending_open_lp_id = None
        ctx.pending_close_lp_id = lp_view.executor_id
        stop_action = StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id)
        return self._transition(
            ctx,
            ControllerState.STOPLOSS_STOP,
            now,
            reason=reason,
            actions=[stop_action],
        )

    def _force_manual_stop(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        ctx.last_exit_reason = "manual_stop"
        if ctx.pending_realized_anchor is None:
            ctx.pending_realized_anchor = ctx.anchor_value_quote
        ctx.cooldown_until_ts = 0.0
        ctx.pending_open_lp_id = None

        lp_view = self._select_lp(snapshot, ctx)
        actions = []
        for swap in snapshot.active_swaps:
            actions.append(StopExecutorAction(controller_id=self._config.id, executor_id=swap.executor_id))

        if lp_view is None or not self._is_lp_open(lp_view):
            self._record_realized_on_close(snapshot, ctx, lp_view, reason="manual_stop")
            ctx.pending_close_lp_id = None
            if self._config.exit_full_liquidation:
                return self._transition(ctx, ControllerState.EXIT_SWAP, now, reason="manual_stop", actions=actions)
            return self._transition(ctx, ControllerState.IDLE, now, reason="manual_stop_complete", actions=actions or None)

        ctx.pending_close_lp_id = lp_view.executor_id
        actions.insert(0, StopExecutorAction(controller_id=self._config.id, executor_id=lp_view.executor_id))
        return self._transition(ctx, ControllerState.STOPLOSS_STOP, now, reason="manual_stop", actions=actions)

    def _anchor_baseline(self, equity: Decimal) -> Decimal:
        cap = max(Decimal("0"), self._config.position_value_quote)
        if cap <= 0:
            return equity
        return min(equity, cap)

    @staticmethod
    def _effective_price(snapshot: Snapshot, lp_view: Optional[LPView]) -> Optional[Decimal]:
        if snapshot.current_price is not None and snapshot.current_price > 0:
            return snapshot.current_price
        return None

    def _update_out_of_range_timer(self, snapshot: Snapshot, ctx: ControllerContext, lp_view: LPView) -> None:
        if not self._is_lp_open(lp_view):
            ctx.out_of_range_since = None
            return
        current_price = snapshot.current_price
        lower_price = lp_view.lower_price
        upper_price = lp_view.upper_price
        if current_price is None or current_price <= 0:
            ctx.out_of_range_since = None
            return
        if lower_price is None or upper_price is None or lower_price <= 0 or upper_price <= 0:
            ctx.out_of_range_since = None
            return
        if lower_price <= current_price <= upper_price:
            ctx.out_of_range_since = None
            return
        if ctx.out_of_range_since is None:
            ctx.out_of_range_since = snapshot.now

    def _guard_price(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[Decision]:
        current_price = snapshot.current_price
        if current_price is not None and current_price > 0:
            return None
        ctx.out_of_range_since = None
        if ctx.state in {
            ControllerState.IDLE,
            ControllerState.ENTRY_OPEN,
            ControllerState.ACTIVE,
            ControllerState.REBALANCE_OPEN,
        }:
            return self._stay(ctx, reason="price_unavailable")
        return None

    def _record_realized_on_close(
        self,
        snapshot: Snapshot,
        ctx: ControllerContext,
        lp_view: Optional[LPView],
        *,
        reason: str,
    ) -> None:
        anchor = ctx.anchor_value_quote or ctx.pending_realized_anchor
        if anchor is None or anchor <= 0:
            return
        current_price = self._effective_price(snapshot, lp_view)
        if current_price is None or current_price <= 0:
            return
        equity = self._compute_risk_equity_value(snapshot, lp_view, current_price, anchor)
        if equity is None:
            return
        ctx.realized_pnl_quote += equity - anchor
        ctx.realized_volume_quote += anchor
        ctx.pending_realized_anchor = None
        self._request_balance_refresh(ctx, snapshot.now, reason="lp_closed")

    def _compute_risk_equity_value(
        self,
        snapshot: Snapshot,
        lp_view: Optional[LPView],
        current_price: Decimal,
        anchor_value_quote: Optional[Decimal],
    ) -> Optional[Decimal]:
        if current_price <= 0:
            return None
        if lp_view is None:
            return None
        return self._estimate_position_value(lp_view, current_price)

    def _select_lp(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[LPView]:
        if ctx.pending_open_lp_id and ctx.pending_open_lp_id in snapshot.lp:
            return snapshot.lp[ctx.pending_open_lp_id]
        if ctx.pending_close_lp_id and ctx.pending_close_lp_id in snapshot.lp:
            return snapshot.lp[ctx.pending_close_lp_id]
        if snapshot.active_lp:
            return min(snapshot.active_lp, key=lambda lp: lp.executor_id)
        if snapshot.lp:
            return min(snapshot.lp.values(), key=lambda lp: lp.executor_id)
        return None

    @staticmethod
    def _select_swap_to_keep(active_swaps: List[SwapView]) -> Optional[SwapView]:
        if not active_swaps:
            return None
        for purpose in (SwapPurpose.EXIT_LIQUIDATION,):
            for swap in active_swaps:
                if swap.purpose == purpose:
                    return swap
        return min(active_swaps, key=lambda swap: swap.executor_id)

    def _is_lp_open(self, lp_view: LPView) -> bool:
        state = lp_view.state
        if state in {
            LPPositionStates.IN_RANGE.value,
            LPPositionStates.OUT_OF_RANGE.value,
        }:
            return True
        if state in {
            LPPositionStates.COMPLETE.value,
            LPPositionStates.NOT_ACTIVE.value,
            LPPositionStates.RETRIES_EXCEEDED.value,
        }:
            return False
        return bool(lp_view.position_address)

    def _is_lp_closed(self, lp_view: LPView) -> bool:
        if lp_view.is_done:
            return True
        if lp_view.state == LPPositionStates.COMPLETE.value:
            return True
        if lp_view.state == LPPositionStates.NOT_ACTIVE.value and not lp_view.position_address:
            return True
        return False

    @staticmethod
    def _is_lp_in_transition(lp_view: LPView) -> bool:
        return lp_view.state in {
            LPPositionStates.OPENING.value,
            LPPositionStates.CLOSING.value,
        }

    def _is_lp_failed(self, lp_view: LPView) -> bool:
        if lp_view.state == LPPositionStates.RETRIES_EXCEEDED.value:
            return True
        if lp_view.close_type == CloseType.FAILED:
            return True
        return False

    def _can_reenter(self, ctx: ControllerContext) -> bool:
        if self._config.reenter_enabled:
            return True
        return ctx.last_exit_reason not in {"stop_loss", "take_profit"}

    def _transition(
        self,
        ctx: ControllerContext,
        next_state: ControllerState,
        now: float,
        reason: str,
        actions: Optional[list] = None,
    ) -> Decision:
        if ctx.state != next_state:
            ctx.state = next_state
            ctx.state_since_ts = now
            if next_state in {ControllerState.IDLE, ControllerState.COOLDOWN}:
                ctx.pending_close_lp_id = None
                ctx.pending_open_lp_id = None
                ctx.pending_swap_id = None
                ctx.pending_swap_since_ts = 0.0
                ctx.pending_swap_purpose = None
                ctx.exit_swap_attempts = 0
                ctx.exit_balance_refresh_attempts = 0
                ctx.out_of_range_since = None
        self._record_decision(ctx, reason)
        return Decision(actions=actions or [], next_state=ctx.state, reason=reason)

    def _stay(self, ctx: ControllerContext, reason: str, actions: Optional[list] = None) -> Decision:
        self._record_decision(ctx, reason)
        return Decision(actions=actions or [], next_state=ctx.state, reason=reason)

    @staticmethod
    def _record_decision(ctx: ControllerContext, reason: str) -> None:
        ctx.last_decision_reason = reason

    def _guard_pending_swap(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[Decision]:
        if not ctx.pending_swap_id:
            return None
        swap = snapshot.swaps.get(ctx.pending_swap_id)
        if swap is not None and not swap.is_done:
            return self._stay(ctx, reason="swap_pending")
        if ctx.pending_swap_since_ts <= 0:
            return self._stay(ctx, reason="swap_pending")
        if (snapshot.now - ctx.pending_swap_since_ts) < self._pending_swap_grace_sec:
            return self._stay(ctx, reason="swap_pending")
        ctx.pending_swap_id = None
        ctx.pending_swap_since_ts = 0.0
        ctx.pending_swap_purpose = None
        return None

    def _find_recent_completed_swap(
        self,
        snapshot: Snapshot,
        ctx: ControllerContext,
        is_exit: bool,
    ) -> Optional[SwapView]:
        if ctx.pending_swap_since_ts <= 0:
            return None
        purposes = {SwapPurpose.EXIT_LIQUIDATION} if is_exit else {SwapPurpose.EXIT_LIQUIDATION}
        candidates = [
            swap for swap in snapshot.swaps.values()
            if swap.purpose in purposes and swap.is_done and swap.timestamp >= ctx.pending_swap_since_ts
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda swap: swap.timestamp)

    def _request_balance_refresh(self, ctx: ControllerContext, now: float, *, reason: str) -> None:
        ttl = max(2, int(self._config.balance_update_timeout_sec))
        deadline = now + ttl
        if deadline > ctx.force_balance_refresh_until_ts:
            ctx.force_balance_refresh_until_ts = deadline
            ctx.force_balance_refresh_reason = reason
