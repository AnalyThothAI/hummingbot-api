---
name: mcp-bot-ops
description: Use when operating bots via this repo's Hummingbot-API MCP tools (swap, deploy-v2, configs, lifecycle) and you need explicit safety guardrails for on-chain actions.
---

# MCP Bot Ops

## Overview
This skill is an executable spec for using the MCP stdio adapter to call Hummingbot-API HTTP endpoints.

## When to use
- Operating Gateway swaps via `gateway_swap_quote` / `gateway_swap_execute`.
- Deploying bots via `deploy_v2_workflow_plan` + `bot_deploy_v2_*`.
- Creating/calibrating controller/script configs via `controller_config_*` / `script_config_*`.
- Lifecycle ops via `bot_status` / `bot_stop` / `bot_stop_and_archive`.

## When NOT to use / Non-goals
- Editing controller Python code or reading/writing YAML files directly (MCP does not read repo files).
- Cross-chain bridging, CEX trading, or strategy parameter "auto-optimization".
- Any guarantee about on-chain execution price matching a quote (quotes can go stale).

## Preconditions
- MCP environment points at a running Hummingbot-API (see `mcp/README.md`).
- Always ensure `gateway_status.running == true` before swaps or deploy actions.

## How MCP Maps To This Repo
MCP is a thin stdio adapter that calls Hummingbot-API HTTP endpoints. It does not read controller modules or YAML files directly.

Where things live (on the Hummingbot-API side):
- Controller modules (Python): `bots/controllers/<controller_type>/<controller_name>.py`
- Global controller configs (YAML): `bots/conf/controllers/<config_name>.yml`
- Bot-scoped controller configs (YAML, per instance): `bots/instances/<bot_name>/conf/controllers/<controller_name>.yml`

Naming rules that must align:
- `controller_type` in YAML must match the folder under `bots/controllers/` (e.g., `generic`).
- `controller_name` in YAML must match the module filename under that folder (e.g., `clmm_lp_uniswap`).
- `id` in YAML should be set, and recommended `id == YAML basename` (without `.yml`).
- `controllers_config` (deploy input) is a list of config names (YAML basenames), not module names.

Discovery tools:
- Global configs: `controller_configs_list_global`, `controller_config_get`
- Bot configs: `controller_configs_list`
- Templates/validation: `controller_config_template`, `controller_config_validate`, `script_config_template`

## MCP Environment Loading
The MCP process reads `.env` automatically if present (see `mcp/server.py:_load_dotenv`). It:
- Loads from the MCP process working directory (run from repo root for predictable behavior).
- Does not override existing environment variables.

## Normalization Rules (avoid ambiguity)
- `network_id` / `chainNetwork` / `gateway_network_id`:
  - Canonical format is `chain-network` (e.g., `ethereum-bsc`, `solana-mainnet-beta`).
  - The deploy planner uses `effective_network_id = network_id or gateway_network_id`.
  - For swaps, `chainNetwork` is the same format and can be reused as `network_id` for approvals.
- Chain type:
  - `chain = chainNetwork.split('-', 1)[0]`.
  - In this repo, allowances/approve are supported only when `chain == "ethereum"` (EVM).
- `connector` vs `spender`:
  - For `gateway_allowances` / `gateway_approve`, `spender` must be either:
    - a connector string that includes a suffix (contains `/`), e.g. `pancakeswap/router`, `uniswap/router`, `uniswap/clmm`, or
    - a direct spender address.
  - Do not strip `pancakeswap/router` into `pancakeswap`; without `/` it is treated as an address and will fail.

## Safety & Risk Policy
### Confirmation Template (required)
Before calling any of:
- `gateway_swap_execute`
- `gateway_approve`
- `gateway_network_config_update`
- `gateway_connector_config_update`
- `bot_deploy_v2_controllers`, `bot_deploy_v2_script`
- `bot_stop`, `bot_stop_and_archive`

Print a confirmation block and require the user to reply `CONFIRM`:
- action/tool
- chainNetwork/network_id
- connector
- baseToken, quoteToken, side, amount
- slippagePct
- quote summary: tokenIn, tokenOut, amountIn, amountOut, minAmountOut, priceImpactPct
- walletAddress (resolved or provided)
- spender (if approvals)
- approve amount (blank => unlimited)
- risk flags: HIGH_PRICE_IMPACT, HIGH_SLIPPAGE, UNLIMITED_APPROVE

### Risk thresholds (must enforce)
- If `priceImpactPct >= 3`: flag HIGH_PRICE_IMPACT and require explicit override in the confirmation.
- If `slippagePct > 1`: flag HIGH_SLIPPAGE and require explicit override in the confirmation.
- If approval amount is omitted/blank: flag UNLIMITED_APPROVE (unlimited approvals are high risk).

### Quote freshness policy
Quote freshness: always re-run `gateway_swap_quote` immediately before execution. Re-quote if user confirmation is delayed (>30s) or after any Gateway state change (token/pool add, restart).

## Workflow A: Unified Swap (Gateway trading/swap)
Inputs: `chainNetwork`, `baseToken`, `quoteToken`, `amount`, `side`, optional `connector`, `slippagePct`, `walletAddress`.
Notes:
- `slippagePct` is 0-100 percent (1 = 1%, 0.01 = 0.01%).
- Example connectors: `pancakeswap/router`, `uniswap/router`, `jupiter/router`.
- If `connector` is omitted, Gateway uses the network's configured `swap_provider` (see `gateway_network_config_get`).
  - Typical defaults in this repo:
    - `solana-mainnet-beta` -> `jupiter/router`
    - `ethereum-base` -> `uniswap/router`
    - `ethereum-bsc` -> `uniswap/router`
  - `pancakeswap/router` is still supported on BSC, but it is not the default in this repo.
  - If you need a non-default DEX on a network (e.g., use `pancakeswap/router` on `ethereum-bsc`), set `connector` explicitly in the swap call or update the network `swap_provider` via `gateway_network_config_update` (then `gateway_restart`).

Steps:
1. Call `gateway_status`.
2. Call `gateway_swap_quote` and summarize: `amountOut`, `minAmountOut`, `priceImpactPct`, `tokenIn`, `tokenOut`, `amountIn`.
3. Determine chain: `chain = chainNetwork.split('-', 1)[0]`.
4. EVM approvals (only when `chain == "ethereum"` and `walletAddress` is known):
   - Set `spender = connector` (use the full string, e.g. `pancakeswap/router` or `uniswap/router`).
   - Call `gateway_allowances` with `{network_id: chainNetwork, address: walletAddress, tokens: [tokenIn], spender}`.
   - Read allowance from `approvals[tokenSymbol]` (or a direct token->allowance map if returned).
   - If allowance is missing/0 or clearly below `amountIn`, request confirmation and call `gateway_approve`:
     - Unlimited: omit/blank `amount` (flag UNLIMITED_APPROVE).
     - Limited: set `amount` to the needed token amount as a string (Gateway parses units using token decimals).
5. Re-quote per quote freshness policy, then request confirmation and call `gateway_swap_execute`.
6. Verify:
   - Use returned tx hash/signature in `gateway_swaps_status` (`transaction_hash`).
   - If not found yet, poll or use `gateway_swaps_search`.

Failure modes & recovery (swap):
- Quote fails with "Token not found": add token via `gateway_token_add`, then `gateway_restart`, then re-quote.
- Allowances/approve fails with "Invalid spender": ensure spender contains `/` (e.g., `pancakeswap/router`) or provide an address.
- Execute returns no tx hash: stop and inspect `gateway_logs`.

## Workflow A.5: Gas & Transaction Settings (Gateway)
Important: Gas/priority fee settings are **not** parameters of `gateway_swap_quote/execute` in this MCP adapter.
They are configured at the Gateway level:
- Network config (per `network_id`): `gateway_network_config_get`, `gateway_network_config_update`
- Connector config (per connector): `gateway_connector_config`, `gateway_connector_config_update`

Suggested process:
1. Inspect current network config with `gateway_network_config_get` using `network_id == chainNetwork` (e.g., `ethereum-base`, `ethereum-bsc`, `solana-mainnet-beta`).
2. If tuning is needed, request confirmation and apply updates via `gateway_network_config_update`.
3. Inspect connector config with `gateway_connector_config` (e.g., `jupiter`, `uniswap`, `pancakeswap`) and update with `gateway_connector_config_update` if needed.
4. After config updates, restart Gateway with `gateway_restart` (recommended) and re-check `gateway_status`.

Common defaults (verify via `gateway_network_config_get`):
- Base (`ethereum-base`): EIP-1559 style with `baseFeeMultiplier` and `priorityFee` (Gateway templates use `baseFeeMultiplier: 1.2`, `priorityFee: 0.001` gwei).
- BSC (`ethereum-bsc`): legacy `gasPrice` (often left blank to auto-fetch from RPC).
- Solana (`solana-mainnet-beta`): `defaultComputeUnits`, `confirmRetryInterval`, `confirmRetryCount`, `minPriorityFeePerCU`.

Solana swap tuning (Jupiter):
- Jupiter connector config includes `priorityLevel` and `maxLamports` (priority fee cap). Use `gateway_connector_config` / `gateway_connector_config_update`.

## Workflow B: Deploy V2 (plan-first)
Minimum required deploy inputs (controllers):
- `instance_name` (string)
- `credentials_profile` (string)
- `controllers_config` (array of YAML basenames, e.g. `["clmm_lp_uniswap"]`)

Optional (recommended when deploying on-chain):
- `gateway_network_id` (chain-network, e.g. `ethereum-bsc`, `solana-mainnet-beta`)
- `gateway_wallet_address` (wallet to set as Gateway default)
- `apply_gateway_defaults` (default true)

1. Call `gateway_status`.
2. Call `deploy_v2_workflow_plan` and inspect: `summary`, `blockers`, `actions`, `notes`.
3. If `blockers` is non-empty: stop and request the missing inputs.
4. Execute `actions[]` in order, but do not run deploy actions unless the planner is ready.
5. If actions include `gateway_token_add` or `gateway_pool_add`:
   - Execute them, then call `gateway_restart`.
   - Poll `gateway_status` until `running == true`.
   - Re-run `deploy_v2_workflow_plan` (required). Do not deploy until `summary.ready` is true and blockers are empty.
6. If actions include `controller_config_upsert` / `script_config_upsert` with `config: {}`:
   - Treat it as a placeholder only. Fill a real config payload first (see Workflow C), then re-run the planner.
7. Request confirmation and run the deploy action (`bot_deploy_v2_controllers` or `bot_deploy_v2_script`).
8. Verify with `bot_instances` and `bot_status`.

## Workflow C: Config Generation & Field Calibration
### Controller configs (global)
Rules:
- `controllers_config` is a list of config names (YAML basenames), not module names.
- Config should include `id`, and recommended `id == YAML basename` (without `.yml`).

Steps:
1. Use `controller_config_template` to fetch the field map (default + required hints).
2. Build a config payload that includes at least:
   - `controller_name`, `controller_type`, `id`
   - connector + trading/pool fields required by the controller (e.g., `connector_name`, `trading_pair`, `pool_address`)
   - For CLMM LP controllers (recommended defaults unless user specifies otherwise):
     - Entry gate: `target_price: 0` (no trigger), keep `trigger_above: true`
     - Exit liquidation: `exit_full_liquidation: true` (only if you want base->quote on exit)
     - Swap risk: `exit_swap_slippage_pct` (ratio, e.g. `0.05` = 5%), `max_exit_swap_attempts: 10`
     - Gas buffer: `native_token_symbol` (e.g. `ETH`/`BNB`/`SOL`) and `min_native_balance`
3. Validate with `controller_config_validate` (fix any 400 errors).
4. Save with `controller_config_upsert` using `config_name` (YAML basename).

### Script configs (global)
1. Use `script_config_template` to fetch defaults.
2. Save with `script_config_upsert` using `config_name`.

## Workflow D: Lifecycle Ops
- Status: `bot_instances`, `bot_status`
- Stop: `bot_stop` (confirm)
- Stop + archive: `bot_stop_and_archive` (confirm)

## Workflow E: CLMM Exit (Close LP -> Optional Swap)
Goal: close a CLMM LP position safely, then (optionally) swap all exposure into a single token (e.g., USDC).

1. If a bot/controller might re-open liquidity, stop it first: `bot_stop` (confirm).
2. If you don't know the position id/address yet, discover it via `gateway_clmm_positions_owned` (optional `pool_address`).
3. Read-only verification (recommended): `gateway_clmm_position_info` with `{connector, network, position_address}`.
   - Use this to confirm token addresses, amounts, pending fees, and whether the range is in-range.
4. Close the position: `gateway_clmm_close` (confirm).
   - This is DB-independent; it can close positions opened by executors/controllers even if the API never recorded them.
5. If you want to consolidate to one token:
   - Follow **Workflow A** (Unified Swap): `gateway_swap_quote` -> re-quote -> `gateway_swap_execute` (confirm).
6. Verify on-chain tx status:
   - CLMM: check Gateway logs, and/or re-run `gateway_clmm_position_info` (expect 404/closed).
   - Swap: `gateway_swaps_status` (tx hash) and poll until confirmed.

## Idempotency Rules (avoid double execution)
- Swap:
  - If a tx hash already exists, do not call `gateway_swap_execute` again; poll status instead.
  - If allowance is sufficient, do not call `gateway_approve`.
- Deploy:
  - If the instance already exists, do not deploy again; use status/stop operations.
  - If the planner says restart is required, do not deploy until restart + re-plan is done.

### Percent Semantics (Avoid Agent Ambiguity)
There are two different "percent" conventions in this stack:
- **Gateway swap tools** (`gateway_swap_quote`, `gateway_swap_execute`): `slippagePct` is **0-100 percent**.
  - `1` = 1%
  - `0.01` = 0.01% (1 bp)
- **Controller YAML** (notably CLMM LP controllers): `*_pct` fields use **ratio** semantics (0-1).
  - `0.3` = 30%
  - `0.05` = 5%

Recommendation: In controller YAML, always use **ratios** for `*_pct` fields, and do **not** use percent-points like `5`/`30` (they are rejected to avoid ambiguity). For swap specifically:
- Gateway `slippagePct: 5` (5%) -> controller `exit_swap_slippage_pct: 0.05` (ratio)

### `target_price` Semantics (CLMM LP Entry Gate)
CLMM LP controllers support an optional entry gate:
- If `target_price` <= 0: entry is always allowed (controller can open immediately).
- If `target_price` > 0: controller stays `IDLE` until the current price crosses the threshold:
  - `trigger_above: true` enters when `price >= target_price`
  - `trigger_above: false` enters when `price <= target_price`

Recommendation: Unless you explicitly want a “wait for price” behavior, set `target_price: 0` in the controller YAML to avoid a bot that looks “running” but never opens.
