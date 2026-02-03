# CLMM LP controller balance snapshots (ledger removed)

Problem
- Symptom: after LP close or exit swap, wallet snapshots can be stale, causing wrong sizing.
- Essence: no ledger reconciliation; controller must rely on refreshed snapshots at key transitions.
- Impact: CLMM LP exit swap sizing and post-close NAV reporting.
- Acceptance: controller requests balance refresh on LP open/close and swap completion; exit swap uses fresh snapshots when available.

Scope
- Snapshot-only balance tracking (no strategy_v2 core changes).
- Applies only to bots/controllers/generic/clmm_lp_*.

Design overview
- Use BalanceManager to schedule refreshes and expose `balance_fresh`.
- Request forced refresh when LP opens/closes and when swaps complete.
- FSM does not gate progress on ledger events.

Data model
- Snapshot: {wallet_base, wallet_quote, balance_fresh, lp views, swaps}.

Data flow
1) BalanceManager refreshes balances on schedule or forced triggers.
2) SnapshotBuilder builds a snapshot each tick.
3) FSM uses snapshot balances for proposals and exit swaps.

FSM gates (key points)
- ENTRY_OPEN / REBALANCE_OPEN: compute from snapshot balances.
- STOPLOSS_STOP / TAKE_PROFIT_STOP: wait for LP close event ack; optional EXIT_SWAP uses snapshot balances.
- EXIT_SWAP: requests a balance refresh once if snapshot is stale.

Reconciliation strategy
- No ledger reconciliation. Fresh snapshots are preferred; stale snapshots trigger a one-time refresh request.

Failure handling
- Missing refresh: exit swap may proceed or be skipped based on retry attempts.
- No event de-duplication required.

Files to modify
- bots/controllers/generic/clmm_lp_domain/clmm_fsm.py (exit swap refresh).
- bots/controllers/generic/clmm_lp_base.py (force refresh on LP open/close).

Atomic tasks (single-commit sized)
1) Ensure forced refresh on LP open/close and swap completion.
2) Keep exit swap retry limits and refresh attempts bounded.

Verification
- Log-based: exit swap uses refreshed balances when available.
- Behavior: if balance stale, FSM issues refresh and continues after completion.
- Metrics: active LP remains single; no duplicate swaps under normal flow.
