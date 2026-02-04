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

## Safety rules (required)
- Always call `gateway_status` before swap or deploy.
- Always ask for confirmation before: `gateway_swap_execute`, `gateway_approve`, `bot_deploy_v2_*`, `bot_stop`, `bot_stop_and_archive`.
- Do not execute deploy actions without `deploy_v2_workflow_plan`.

## Swap flow (unified trading/swap)
Inputs: `chainNetwork`, `baseToken`, `quoteToken`, `amount`, `side`, optional `connector`, `slippagePct`, `walletAddress`.
1. `gateway_swap_quote` and summarize `amountOut`, `priceImpactPct`, `minAmountOut`.
2. Confirm execution parameters (amount, slippage, connector, wallet).
3. If EVM allowance error:
   - `gateway_allowances` with `{network_id: chainNetwork, address: walletAddress, tokens: [baseToken], spender}`
   - Ask before `gateway_approve`.
   - Spender is typically connector name without `/type` (e.g., `pancakeswap`, `uniswap`). If unsure, ask.
4. `gateway_swap_execute`.
5. Verify via `gateway_swaps_status` or `gateway_swaps_search`.

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
