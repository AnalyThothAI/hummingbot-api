# CLMM LP Anchor Equity Baseline

## Summary
Use the actual LP equity right after the position opens as the anchor baseline for stoploss/take-profit.
Anchor is set once per cycle and persists across rebalance and IDLE until stoploss exit completes.
Rebalance/open budget uses the anchor (not `position_value_quote`) to prevent hidden reinvestment.

## Goals
- Anchor equals real LP equity at the moment the LP becomes active.
- Stoploss and take-profit compare against the anchor value without implicit budget caps.
- Rebalance uses anchor as the max budget so profits are not auto-reinvested.
- Keep FSM explicit and avoid new coupling or hidden state transitions.

## Non-goals
- No change to executor behaviors or connector logic.
- No wallet isolation or external deposit tracking.
- No new configuration fields.

## Data Flow
1. Snapshot (wallet, LP, swaps, price) is produced.
2. FSM sees LP become active and sets anchor using LP equity.
3. Open proposal uses `min(anchor, wallet_value)` as effective budget.
4. Stoploss/TP use equity = LP value (no wallet inclusion).

## FSM Changes
- `ENTRY_OPEN/REBALANCE_OPEN/ACTIVE` set anchor once when LP becomes active.
- `IDLE` retains anchor to allow stoploss if LP open fails after a reopen attempt.
- Stoploss remains higher priority than rebalance.

## Budget Rules
- `anchor_value_quote` is the primary budget after the first LP opens.
- `position_value_quote` only used before anchor exists.
- Rebalance open proposal never exceeds `anchor` even if wallet grows.

## Edge Cases
- If price is unavailable, anchor is not set and stoploss is skipped.
- External wallet deposits will affect equity; no offset tracking is added.
- If LP open fails after a reopen attempt, anchor persists and stoploss can still trigger.

## Testing
- Unit: simulate LP open success, ensure anchor set and persists.
- FSM: verify stoploss triggers in IDLE with anchor set.
- Proposal: verify open budget uses anchor, not position_value_quote.
