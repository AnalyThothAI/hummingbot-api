from decimal import Decimal
from typing import Callable, Optional, Tuple

from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

from .components import ControllerContext, Decision, DecisionPatch, Intent, IntentFlow, IntentStage, Snapshot

ComputeInventoryDelta = Callable[
    [Optional[Decimal], Decimal, Decimal],
    Tuple[Optional[Tuple[Decimal, Decimal]], Optional[str], Optional[Tuple[Decimal, Decimal]]],
]
MaybePlanInventorySwap = Callable[..., Optional[Decision]]
BuildOpenLPAction = Callable[[Optional[Decimal], Decimal, Decimal], Optional[CreateExecutorAction]]
PatchMutator = Callable[[DecisionPatch, CreateExecutorAction], None]


def plan_open(
    *,
    snapshot: Snapshot,
    ctx: ControllerContext,
    flow: IntentFlow,
    reason: str,
    compute_inventory_delta: ComputeInventoryDelta,
    maybe_plan_inventory_swap: MaybePlanInventorySwap,
    build_open_lp_action: BuildOpenLPAction,
    patch_mutator: Optional[PatchMutator] = None,
) -> Decision:
    delta, reason_override, open_amounts = compute_inventory_delta(
        snapshot.current_price,
        snapshot.wallet_base,
        snapshot.wallet_quote,
    )
    if delta is None:
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.WAIT, reason=reason_override or "insufficient_balance"),
        )
    delta_base, delta_quote_value = delta

    swap_plan = maybe_plan_inventory_swap(
        now=snapshot.now,
        ctx=ctx,
        current_price=snapshot.current_price,
        delta_base=delta_base,
        delta_quote_value=delta_quote_value,
        flow=flow,
    )
    if swap_plan is not None:
        return swap_plan

    if open_amounts is None:
        return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="insufficient_balance"))
    base_amt, quote_amt = open_amounts
    action = build_open_lp_action(snapshot.current_price, base_amt, quote_amt)
    if action is None:
        return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="budget_unavailable"))

    patch = DecisionPatch()
    patch.swap.awaiting_balance_refresh = True
    if patch_mutator is not None:
        patch_mutator(patch, action)

    return Decision(
        intent=Intent(flow=flow, stage=IntentStage.SUBMIT_LP, reason=reason),
        actions=[action],
        patch=patch,
    )
