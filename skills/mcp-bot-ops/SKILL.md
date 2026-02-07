---
name: mcp-bot-ops
description: Use when operating bots via Hummingbot-API MCP: plan-first deploy-v2, unified swap, and lifecycle (status/stop/archive).
---

# MCP Bot Ops

## Scope
Covers three workflows via MCP tools:
- Unified swap (quote/execute)
- Deploy V2 (plan-first, controllers or scripts)
- Bot lifecycle (status/stop/archive)

## How MCP Maps To This Repo
MCP is just a stdio adapter that calls the Hummingbot-API HTTP endpoints. It does **not** read controllers/configs directly.

Where things live (on the Hummingbot-API side):
- Controller modules (Python): `bots/controllers/<controller_type>/<controller_name>.py`
- Global controller configs (YAML): `bots/conf/controllers/<config_name>.yml`
- Bot-scoped controller configs (YAML, per instance): `bots/instances/<bot_name>/conf/controllers/<controller_name>.yml`

Naming rules that must align:
- `controller_type` in YAML must match the folder under `bots/controllers/` (e.g. `generic`).
- `controller_name` in YAML must match the module filename under that folder (e.g. `clmm_lp_uniswap`).
- `id` in YAML should be set and **recommended** to equal the YAML basename (without `.yml`).
  - If `id` is missing/empty, the bot may report `controller_id=null`, which breaks config mapping in the dashboard (units fall back to `Quote`, many fields show `-`).
- `controllers_config` (deploy input) is a list of **config names** (YAML basenames), not module names.

Discovery tools:
- Global configs: `controller_configs_list_global`
- Bot configs: `controller_configs_list`
- Validate a config payload: `controller_config_validate` (needs `controller_type` + `controller_name`)

## MCP Environment Loading
The MCP process reads `.env` automatically if present (see `mcp/server.py:_load_dotenv`). It:
- Loads from the MCP process working directory (run from repo root for predictable behavior).
- Does **not** override existing environment variables.

Common ports:
- API via `make run` (uvicorn): `http://127.0.0.1:8000`
- API via `make deploy` (docker-compose): `http://127.0.0.1:18000` (host port mapping)

## Safety rules (required)
- Always call `gateway_status` before swap or deploy.
- Always ask for confirmation before: `gateway_swap_execute`, `gateway_approve`, `bot_deploy_v2_*`, `bot_stop`, `bot_stop_and_archive`.
- Do not execute deploy actions without `deploy_v2_workflow_plan`.

## Swap flow (unified trading/swap)
Inputs: `chainNetwork`, `baseToken`, `quoteToken`, `amount`, `side`, optional `connector`, `slippagePct`, `walletAddress`.
Slippage is a percentage (0-100). `1` means 1%. `0.01` means 0.01% (1 bp).
1. `gateway_swap_quote` and summarize `amountOut`, `priceImpactPct`, `minAmountOut`.
2. Confirm execution parameters (amount, slippage, connector, wallet).
3. If `chainNetwork` starts with `ethereum-`, handle allowance before execute when `walletAddress` is known.
4. Derive token-in: `SELL` uses `baseToken`, `BUY` uses `quoteToken`.
5. Derive spender: if `connector` is set, use the part before `/` (e.g., `pancakeswap/router` -> `pancakeswap`). If unknown, ask.
6. Pre-check allowance (EVM only): `gateway_allowances` with `{network_id: chainNetwork, address: walletAddress, tokens: [token_in], spender}`.
7. If allowance is missing or below required, ask for approval and call `gateway_approve` with no `amount` (unlimited).
8. `gateway_swap_execute`.
9. Verify via `gateway_swaps_status` or `gateway_swaps_search`.

## Deploy V2 flow (plan-first)
Inputs: `deployment_type`, `instance_name`, `credentials_profile`, `network_id` or `gateway_network_id`, `connector_name`, `pool_type`, `pool_address` or token addresses, `tokens`, optional `wallet_address`, `spender`, plus `controllers_config` or `script` + `script_config`.
1. If token metadata is missing, call `metadata_token`.
2. Run `deploy_v2_workflow_plan` and inspect `blockers`, `actions`, `notes`.
3. If `blockers` exist, stop and request missing inputs.
4. Execute `actions` in order.
5. If actions include `gateway_token_add` or `gateway_pool_add`, run `gateway_restart` and re-run the plan before continuing.
6. When `summary.ready` is true, call `bot_deploy_v2_controllers` or `bot_deploy_v2_script`.
7. Validate with `bot_instances` and `bot_status`.

## Lifecycle ops
- Status: `bot_status`, `bot_instances`
- Stop: `bot_stop` (confirm)
- Stop + archive: `bot_stop_and_archive` (confirm)

## Config notes
- `controller_name` must match module filename; `controller_type` must match `controllers/<type>/`.
- Prefer `pool_address` for deterministic pool matching.
- Use `controller_config_*` or `script_config_*` to manage configs.

### Percent Semantics (Avoid Agent Ambiguity)
There are two different "percent" conventions in this stack:
- **Gateway swap tools** (`gateway_swap_quote`, `gateway_swap_execute`): `slippagePct` is **0-100 percent**.
  - `1` = 1%
  - `0.01` = 0.01% (1 bp)
- **Controller YAML** (notably CLMM LP controllers): many `*_pct` fields are accepted as either:
  - ratio: `0.3` (30%)
  - percent-points: `30` (30%)

Recommendation: In controller YAML, always use **percent-points** for `*_pct` fields (e.g., `position_width_pct: 30`, `stop_loss_pnl_pct: 30`, `take_profit_pnl_pct: 10`, `exit_swap_slippage_pct: 1`) to avoid confusing it with Gateway `slippagePct`.

### `target_price` Semantics (CLMM LP Entry Gate)
CLMM LP controllers support an optional entry gate:
- If `target_price` <= 0: entry is always allowed (controller can open immediately).
- If `target_price` > 0: controller stays `IDLE` until the current price crosses the threshold:
  - `trigger_above: true` enters when `price >= target_price`
  - `trigger_above: false` enters when `price <= target_price`

Recommendation: Unless you explicitly want a “wait for price” behavior, set `target_price: 0` in the controller YAML to avoid a bot that looks “running” but never opens.
