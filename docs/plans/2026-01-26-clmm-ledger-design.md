# CLMM LP controller local ledger (hybrid 120s)

Problem
- Symptom: after LP close/rebalance, inventory swaps can use stale wallet snapshots, leading to wrong swap direction and rapid quote depletion.
- Essence: no single balance truth for the controller; executor events and connector snapshots are unsynchronized.
- Impact: CLMM LP controller state machine (entry/rebalance/stoploss) and swap sizing/side decisions.
- Acceptance: actions only advance when the ledger confirms the prior action or reconciliation completes; no repeated wrong-direction swaps.

Scope
- Controller-local ledger only (no strategy_v2 core changes). Optional: add timestamp to LP executor balance_event.
- Applies only to bots/controllers/generic/clmm_lp_*.

Design overview
- Maintain a ledger that ingests executor balance events (LP open/close deltas, swap deltas).
- Use ledger balances for action planning. Wallet snapshots are only used for reconciliation after a 120s window.
- State machine gates progress on event acknowledgement; if events are missing beyond the window, pause and reconcile.

Data model
- BalanceEvent: {executor_id, seq, timestamp, kind, delta_base, delta_quote}.
- LedgerState: {balance_base, balance_quote, last_event_ts, seen_event_ids, pending_action_id, pending_action_ts}.
- Snapshot extends with balance_events and swap_events.

Data flow
1) SnapshotBuilder parses executor custom_info into BalanceEvent list.
2) Ledger applies events with de-duplication and ordering.
3) FSM uses ledger balances for proposals and waits for event ack.
4) If no ack within 120s, FSM enters SYNC_WAIT (or stays with reason=ledger_stale) and waits for reconciliation.

FSM gates (key points)
- ENTRY_SWAP / REBALANCE_SWAP / STOPLOSS_SWAP: compute from ledger balance; record pending action; wait for swap delta or completion event.
- ENTRY_OPEN / REBALANCE_OPEN: compute from ledger balance; wait for LP open event ack.
- REBALANCE_STOP / STOPLOSS_STOP: wait for LP close event ack.

Reconciliation strategy (hybrid 120s)
- If now - last_event_ts <= 120s: ledger is authoritative.
- If >120s: require snapshot to be "close enough" (epsilon) before allowing new actions.
- If reconciliation fails repeatedly, log ledger_stale and pause actions.

Failure handling
- Missing events: time out into reconciliation.
- Duplicate events: ignore via (executor_id, seq) or timestamp-based ids.
- Out-of-order events: ignore if timestamp < last_event_ts.

Files to modify
- bots/controllers/generic/clmm_lp_domain/components.py (BalanceEvent, LedgerState).
- bots/controllers/generic/clmm_lp_domain/io.py (extract balance_event and swap deltas).
- bots/controllers/generic/clmm_lp_domain/ledger.py (new).
- bots/controllers/generic/clmm_lp_domain/clmm_fsm.py (gates and SYNC_WAIT logic).
- bots/controllers/generic/clmm_lp_base.py (use ledger balances instead of BalanceManager for planning).
- Optional: hummingbot/hummingbot/strategy_v2/executors/lp_position_executor/lp_position_executor.py (add timestamp to balance_event).

Atomic tasks (single-commit sized)
1) Add ledger data model and event extraction.
2) Integrate ledger into FSM gates for swap/open/stop.
3) Wire controller to use ledger balances for planning and NAV.
4) Optional: add balance_event timestamp to LP executor.

Verification
- Log-based: no repeated inventory swaps after LP close; swap direction matches post-close balances.
- Behavior: if events missing, FSM pauses and resumes after snapshot reconciliation.
- Metrics: active LP remains single; no duplicate swaps under normal event flow.
