# CLMM LP Anchor Equity Baseline

## Summary
Use the actual equity right after the entry inventory swap as the anchor baseline for stoploss/take-profit.
Anchor is set once per cycle and persists across rebalance and IDLE until stoploss exit completes.
Rebalance/open budget uses the anchor (not `position_value_quote`) to prevent hidden reinvestment.

## Goals
- Anchor equals real entry equity at the moment the inventory swap completes.
- Stoploss and take-profit compare against the anchor value without implicit budget caps.
- Rebalance uses anchor as the max budget so profits are not auto-reinvested.
- Keep FSM explicit and avoid new coupling or hidden state transitions.

## Non-goals
- No change to executor behaviors or connector logic.
- No wallet isolation or external deposit tracking.
- No new configuration fields.

## Data Flow
1. Snapshot (wallet, LP, swaps, price) and ledger events are produced.
2. FSM resolves pending inventory swap using ledger events.
3. On swap completion, anchor is set using wallet equity (LP is zero at this point).
4. Open proposal uses `min(anchor, wallet_value)` as effective budget.
5. Stoploss/TP use equity = LP value + wallet value (no budget caps).

## FSM Changes
- `ENTRY_SWAP` -> on swap completion, set anchor if missing.
- `ENTRY_OPEN/REBALANCE_OPEN/ACTIVE` no longer update anchor once set.
- `IDLE` retains anchor to allow stoploss if LP open fails after swap.
- Stoploss remains higher priority than rebalance.

## Budget Rules
- `anchor_value_quote` is the primary budget after entry swap.
- `position_value_quote` only used before anchor exists.
- Rebalance open proposal never exceeds `anchor` even if wallet grows.

## Edge Cases
- If price is unavailable, anchor is not set and stoploss is skipped.
- External wallet deposits will affect equity; no offset tracking is added.
- If LP open fails after swap, anchor persists and stoploss can still trigger.

## Testing
- Unit: simulate entry swap success, ensure anchor set and persists.
- FSM: verify stoploss triggers in IDLE with anchor set.
- Proposal: verify open budget uses anchor, not position_value_quote.
