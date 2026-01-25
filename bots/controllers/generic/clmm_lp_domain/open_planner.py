from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional, Tuple

from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

from .components import ControllerContext, Decision, DecisionPatch, Intent, IntentFlow, IntentStage, Snapshot

MaybePlanInventorySwap = Callable[..., Optional[Decision]]
BuildOpenLPAction = Callable[["OpenProposal", float], Optional[CreateExecutorAction]]
PatchMutator = Callable[[DecisionPatch, CreateExecutorAction], None]


@dataclass(frozen=True)
class OpenProposal:
    lower: Decimal
    upper: Decimal
    target_base: Decimal
    target_quote: Decimal
    delta_base: Decimal
    delta_quote_value: Decimal
    open_base: Decimal
    open_quote: Decimal
    min_swap_value_quote: Decimal


BuildOpenProposal = Callable[
    [Optional[Decimal], Decimal, Decimal],
    Tuple[Optional[OpenProposal], Optional[str]],
]


def plan_open(
    *,
    snapshot: Snapshot,
    ctx: ControllerContext,
    flow: IntentFlow,
    reason: str,
    build_open_proposal: BuildOpenProposal,
    maybe_plan_inventory_swap: MaybePlanInventorySwap,
    build_open_lp_action: BuildOpenLPAction,
    patch_mutator: Optional[PatchMutator] = None,
) -> Decision:
    proposal, reason_override = build_open_proposal(
        snapshot.current_price,
        snapshot.wallet_base,
        snapshot.wallet_quote,
    )
    if proposal is None:
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.WAIT, reason=reason_override or "insufficient_balance"),
        )

    swap_plan = maybe_plan_inventory_swap(
        now=snapshot.now,
        ctx=ctx,
        current_price=snapshot.current_price,
        delta_base=proposal.delta_base,
        delta_quote_value=proposal.delta_quote_value,
        min_swap_value=proposal.min_swap_value_quote,
        flow=flow,
    )
    if swap_plan is not None:
        return swap_plan

    action = build_open_lp_action(proposal, snapshot.now)
    if action is None:
        return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="budget_unavailable"))

    patch = DecisionPatch()
    if patch_mutator is not None:
        patch_mutator(patch, action)

    return Decision(
        intent=Intent(flow=flow, stage=IntentStage.SUBMIT_LP, reason=reason),
        actions=[action],
        patch=patch,
    )
