# CLMM LP Controller FSM Refactor Design
Date: 2026-01-26
Status: approved

## Context
The current controller uses an implicit rule chain and patch-based updates across multiple components. Balance
sync relies on event deltas and barriers, which can block rebalances and stoploss flows. The goal is a clear,
explicit state machine with lower coupling and fewer hidden gates.

## Goals
- Replace implicit rule ordering with an explicit FSM.
- Support a single active LP position per controller.
- Keep entry, rebalance, stoploss, budget, and cost filter behavior.
- Simplify balance management to a snapshot service with minimal blocking.
- Reduce coupling between stoploss, rebalance, and balance syncing logic.

## Non-Goals
- Backward compatibility with the old controller internal structures.
- Multi-LP support.
- Manual kill switch and failure-blocked gating.

## Proposed Architecture
- `clmm_lp_base.py` becomes a thin controller: build snapshot, refresh balances, call FSM, send actions.
- New `clmm_lp_domain/fsm.py` encapsulates state transitions and action decisions.
- `rebalance_engine.py` becomes a pure predicate helper (no plan queues).
- `BalanceManager` becomes a balance snapshot service (no event deltas, no barriers).
- `components.py` is simplified to core view and decision types only.

## Controller FSM
States:
- IDLE
- ENTRY_OPEN
- ENTRY_SWAP
- ACTIVE
- REBALANCE_STOP
- REBALANCE_SWAP
- REBALANCE_OPEN
- STOPLOSS_STOP
- STOPLOSS_SWAP
- COOLDOWN

Key transitions:
- IDLE -> ENTRY_OPEN when entry conditions true and balance snapshot is fresh.
- ENTRY_OPEN -> ACTIVE after LP becomes active.
- ENTRY_OPEN -> ENTRY_SWAP if inventory swap is required.
- ENTRY_SWAP -> ENTRY_OPEN after swap completes.
- ACTIVE -> REBALANCE_STOP when rebalance predicate is true.
- REBALANCE_STOP -> REBALANCE_SWAP after LP is fully closed.
- REBALANCE_SWAP -> REBALANCE_OPEN after swap completes or if no swap needed.
- REBALANCE_OPEN -> ACTIVE after LP becomes active.
- ACTIVE -> STOPLOSS_STOP when stoploss triggers.
- STOPLOSS_STOP -> STOPLOSS_SWAP after LP is fully closed.
- STOPLOSS_SWAP -> COOLDOWN after full base is swapped to quote.
- COOLDOWN -> IDLE after cooldown expires (if reenter disabled, remain IDLE).

The FSM stores `state`, `state_since_ts`, and a small set of context fields to avoid hidden control flow.

## Balance Manager (Snapshot Service)
Responsibilities:
- Periodically call `connector.update_balances()` and store base/quote available balances.
- Track `last_update_ts` and provide `is_fresh(now)` for gating entry or swap actions.

Behavior:
- Only entry and rebalance swap/open states require fresh balances.
- All other FSM states proceed without blocking on balance freshness.
- No balance delta events, no barriers, no optimistic adjustments.

## Stoploss Simplification
Anchor calculation:
- On entering ACTIVE, record anchor as `lp_value + wallet_value` at that time, capped by budget.

Stoploss flow:
- Triggered when equity <= anchor * (1 - stop_loss_pnl_pct).
- Stop LP, then sell all wallet base into quote via a swap.
- No liquidation target tracking, no pending flags, no reliance on balance events.

## Rebalance Simplification
`RebalanceEngine` becomes pure logic:
- `should_rebalance(snapshot, ctx)` returns a boolean and reason.
- Conditions retain hysteresis, out_of_range_since, rebalance_seconds, cooldown, and cost filter.

The FSM handles stop, swap, and open sequencing explicitly.

## Core Data Structures
`Snapshot`:
- now, current_price, wallet_base, wallet_quote
- lp, swaps, active_lp, active_swaps

`ControllerContext`:
- state, state_since_ts
- cooldown_until_ts
- anchor_value_quote
- last_rebalance_ts, rebalance_timestamps
- pending_lp_id, pending_swap_id
- stoploss_swap_attempts, last_decision_reason

`Decision`:
- actions
- next_state
- reason

## Error Handling
- Failures log and retry with cooldown, no global blocking.
- Stoploss swap failures retry up to max attempts, then enter COOLDOWN with a reason.
- Rebalance open failures retry in REBALANCE_OPEN after cooldown.

## Observability
`get_custom_info()` provides:
- state, state_since, last_reason
- anchor_value_quote, cooldown_remaining
- rebalance_due, balance_fresh
- active_lp_count, active_swap_count

## Migration Plan
1) Add new FSM and simplified context/types.
2) Rewrite `clmm_lp_base.py` to delegate decisions to FSM.
3) Simplify `rebalance_engine.py`.
4) Replace BalanceManager with snapshot-only behavior.
5) Remove old patch/reconcile/regions machinery.

## Testing Plan
- FSM transitions: entry, rebalance, stoploss, cooldown.
- Balance freshness gate only blocks entry and swap.
- CostFilter + hysteresis boundary cases.

## Acceptance Criteria
- No implicit rule ordering in controller.
- Rebalance and stoploss do not block on missing balance events.
- Entry and rebalance only require fresh balances at action time.
- Single active LP enforced by FSM behavior.
