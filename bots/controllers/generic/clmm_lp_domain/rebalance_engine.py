from decimal import Decimal
from typing import Callable, Optional

from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction

from .components import (
    ControllerContext,
    Decision,
    DecisionPatch,
    Intent,
    IntentFlow,
    IntentStage,
    LPView,
    LpContext,
    RebalancePlan,
    RebalanceStage,
    Snapshot,
)
from .cost_filter import CostFilter
from .open_planner import BuildOpenLPAction, BuildOpenProposal, MaybePlanInventorySwap, plan_open

EstimatePositionValue = Callable[[LPView, Decimal], Decimal]
OutOfRangeDeviationPct = Callable[[Decimal, Decimal, Decimal], Decimal]
CanRebalanceNow = Callable[[float, ControllerContext], bool]
SwapSlippagePct = Callable[[], Decimal]


class RebalanceEngine:
    def __init__(
        self,
        *,
        controller_id: str,
        config,
        estimate_position_value: EstimatePositionValue,
        out_of_range_deviation_pct: OutOfRangeDeviationPct,
        can_rebalance_now: CanRebalanceNow,
        swap_slippage_pct: SwapSlippagePct,
        build_open_proposal: BuildOpenProposal,
        maybe_plan_inventory_swap: MaybePlanInventorySwap,
        build_open_lp_action: BuildOpenLPAction,
    ) -> None:
        self._controller_id = controller_id
        self._config = config
        self._estimate_position_value = estimate_position_value
        self._out_of_range_deviation_pct = out_of_range_deviation_pct
        self._can_rebalance_now = can_rebalance_now
        self._swap_slippage_pct = swap_slippage_pct
        self._build_open_proposal = build_open_proposal
        self._maybe_plan_inventory_swap = maybe_plan_inventory_swap
        self._build_open_lp_action = build_open_lp_action

    def reconcile(self, snapshot: Snapshot, ctx: ControllerContext) -> None:
        now = snapshot.now
        for executor_id, plan in list(ctx.rebalance.plans.items()):
            if plan.stage == RebalanceStage.OPEN_REQUESTED:
                open_id = plan.open_executor_id
                open_lp = snapshot.lp.get(open_id) if open_id else None
                if open_lp and (
                    open_lp.position_address
                    or open_lp.state in {LPPositionStates.IN_RANGE.value, LPPositionStates.OUT_OF_RANGE.value}
                ):
                    ctx.swap.awaiting_balance_refresh = True
                    ctx.rebalance.plans.pop(executor_id, None)
                    continue
            elif plan.stage == RebalanceStage.STOP_REQUESTED:
                old_lp = snapshot.lp.get(executor_id)
                if old_lp is None or not old_lp.is_active:
                    ctx.swap.awaiting_balance_refresh = True
                    ctx.rebalance.plans[executor_id] = RebalancePlan(
                        stage=RebalanceStage.WAIT_REOPEN,
                        reopen_after_ts=plan.reopen_after_ts,
                        requested_at_ts=plan.requested_at_ts,
                    )
            elif plan.stage == RebalanceStage.WAIT_REOPEN:
                old_lp = snapshot.lp.get(executor_id)
                if old_lp is not None and old_lp.is_active:
                    ctx.rebalance.plans[executor_id] = RebalancePlan(
                        stage=RebalanceStage.STOP_REQUESTED,
                        reopen_after_ts=plan.reopen_after_ts,
                        requested_at_ts=plan.requested_at_ts if plan.requested_at_ts > 0 else now,
                    )

    def decide_stop(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[Decision]:
        current_price = snapshot.current_price
        now = snapshot.now
        stop_actions = []
        patch = DecisionPatch()

        for executor_id, plan in ctx.rebalance.plans.items():
            if plan.stage != RebalanceStage.STOP_REQUESTED:
                continue
            lp_view = snapshot.lp.get(executor_id)
            if lp_view is None or not lp_view.is_active:
                continue
            if lp_view.in_transition:
                continue
            stop_actions.append(StopExecutorAction(controller_id=self._controller_id, executor_id=executor_id))

        for lp_view in snapshot.active_lp:
            if lp_view.executor_id in ctx.rebalance.plans:
                continue
            if lp_view.in_transition:
                continue

            lower_price = lp_view.lower_price
            upper_price = lp_view.upper_price
            if lower_price is None or upper_price is None or lower_price <= 0 or upper_price <= 0:
                continue

            effective_price = current_price if current_price is not None else lp_view.current_price
            if effective_price is None or effective_price <= 0:
                continue

            if lower_price <= effective_price <= upper_price:
                continue

            deviation_pct = self._out_of_range_deviation_pct(effective_price, lower_price, upper_price)
            hysteresis_pct = max(Decimal("0"), self._config.hysteresis_pct)
            if deviation_pct < (hysteresis_pct * Decimal("100")):
                continue

            out_of_range_since = lp_view.out_of_range_since
            if out_of_range_since is None:
                continue
            if (now - out_of_range_since) < self._config.rebalance_seconds:
                continue
            if (now - ctx.rebalance.last_rebalance_ts) < self._config.cooldown_seconds:
                continue
            if not self._can_rebalance_now(now, ctx):
                continue

            fee_rate_ewma = ctx.lp.get(lp_view.executor_id, LpContext()).fee.fee_rate_ewma
            allow_rebalance = CostFilter.allow_rebalance(
                enabled=self._config.cost_filter_enabled,
                position_value=self._estimate_position_value(lp_view, effective_price),
                fee_rate_ewma=fee_rate_ewma,
                fee_rate_bootstrap_quote_per_hour=self._config.cost_filter_fee_rate_bootstrap_quote_per_hour,
                auto_swap_enabled=self._config.auto_swap_enabled,
                swap_slippage_pct=self._swap_slippage_pct(),
                fixed_cost_quote=self._config.cost_filter_fixed_cost_quote,
                max_payback_sec=self._config.cost_filter_max_payback_sec,
            )
            if not allow_rebalance and CostFilter.should_force_rebalance(
                now=now,
                out_of_range_since=out_of_range_since,
                rebalance_seconds=self._config.rebalance_seconds,
            ):
                allow_rebalance = True
            if not allow_rebalance:
                continue

            stop_actions.append(StopExecutorAction(controller_id=self._controller_id, executor_id=lp_view.executor_id))
            patch.rebalance.add_plans[lp_view.executor_id] = RebalancePlan(
                stage=RebalanceStage.STOP_REQUESTED,
                reopen_after_ts=now + self._config.reopen_delay_sec,
                requested_at_ts=now,
            )
            patch.rebalance.record_rebalance_ts = now

        if not stop_actions:
            return None
        patch.swap.awaiting_balance_refresh = True
        return Decision(
            intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.STOP_LP, reason="out_of_range_rebalance"),
            actions=stop_actions,
            patch=patch,
        )

    def decide_reopen_or_wait(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[Decision]:
        if not ctx.rebalance.plans:
            return None
        if any(plan.stage == RebalanceStage.OPEN_REQUESTED for plan in ctx.rebalance.plans.values()):
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="open_in_progress"))

        decision = self._decide_rebalance_reopen(snapshot, ctx)
        if decision is not None:
            return decision
        return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="rebalance_wait"))

    def _decide_rebalance_reopen(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[Decision]:
        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="price_unavailable"))

        eligible_ids = [
            executor_id
            for executor_id, plan in ctx.rebalance.plans.items()
            if plan.stage == RebalanceStage.WAIT_REOPEN
            and plan.open_executor_id is None
            and snapshot.now >= plan.reopen_after_ts
            and not (snapshot.lp.get(executor_id) and snapshot.lp[executor_id].is_active)
        ]
        if not eligible_ids:
            return None

        executor_id = sorted(eligible_ids, key=lambda i: ctx.rebalance.plans[i].reopen_after_ts)[0]

        def _patch_reopen(patch: DecisionPatch, action) -> None:
            prev_plan = ctx.rebalance.plans.get(executor_id)
            reopen_after_ts = prev_plan.reopen_after_ts if prev_plan is not None else snapshot.now
            patch.rebalance.add_plans[executor_id] = RebalancePlan(
                stage=RebalanceStage.OPEN_REQUESTED,
                reopen_after_ts=reopen_after_ts,
                open_executor_id=action.executor_config.id,
                requested_at_ts=snapshot.now,
            )

        return plan_open(
            snapshot=snapshot,
            ctx=ctx,
            flow=IntentFlow.REBALANCE,
            reason="rebalance_open",
            build_open_proposal=self._build_open_proposal,
            maybe_plan_inventory_swap=self._maybe_plan_inventory_swap,
            build_open_lp_action=self._build_open_lp_action,
            patch_mutator=_patch_reopen,
        )
