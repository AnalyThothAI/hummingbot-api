# Gateway Pools UX Optimization (LP Experience & Add-Pool Flow)

## 0. Pre-change Summary
- Problem: Pools page repeats search/browse flows; default sort is not LP-friendly; Meteora search can fail and surface 502.
- Essence: unclear primary flow + missing preferences (24h volume sorting, Meteora bin 80/100 preference).
- Scope: Dashboard Pools UI and metadata `/metadata/pools` fallback logic.
- Acceptance: searching `GB8KtQfMChhYrCYtd5PoAB42kAdkHnuyAincSSmFpump` + `SOL` returns Meteora pools; default sort is 24h volume; bins 80/100 are preferred; page no longer feels duplicated.

## 1. Goals
- LP-first view: highlight Volume/TVL/APR/APY, default sort by 24h volume.
- Convenience: search -> select -> one-click fill Add Pool.
- Convenience: auto-fill token addresses from Gateway token list when user types symbols.
- Stability: avoid 502 by Meteora fallback when Gecko is unavailable.

## 2. Non-goals
- No new cache or cron jobs.
- No change to Gateway data sources or APIs.

## 3. Approach
- Merge Pools UI into a single "Pool Finder" with a data source toggle (Search Results / Existing Pools).
- Apply default sorting by 24h volume and a soft preference for Meteora bins 80/100.
- Backend metadata: fix chain/network parsing; fallback to Meteora results when Gecko fails; merge and enrich where possible.

## 4. Changes
### 4.1 Dashboard Pools UI
- Single primary list; remove duplicate "Pools by Network" section.
- Data Source toggle: Search Results vs Existing Pools.
- Unified search inputs (Token A/B/Search) + explicit Search button.
- Filters for Search Results only; default sort = Volume 24h (desc).
- Meteora bin preference toggle (soft).
- Existing Pools uses a simplified table (no live metrics).
- Selected pool can autofill Add Pool form.
- When typing Base/Quote symbols, addresses auto-fill from the network token list after a short debounce.

### 4.2 Backend `/metadata/pools`
- Fix chain/network parsing to avoid runtime errors.
- If Gecko fails and Meteora data exists, return Meteora results.
- Merge Gecko + Meteora fields (bin_step, fee, etc.).

## 5. Risks & Rollback
- Risk: Meteora-only pools may lack symbols; user may need to fill symbols manually.
- Rollback: revert Pools UI layout; revert `/metadata/pools` fallback to Gecko-only.

## 6. Acceptance Checklist
- Pools page shows a single primary list without duplication.
- Default sorting is 24h volume descending.
- Meteora bins 80/100 appear at the top (soft preference).
- Query `GB8KtQfMChhYrCYtd5PoAB42kAdkHnuyAincSSmFpump` + `SOL` returns Meteora pools.
