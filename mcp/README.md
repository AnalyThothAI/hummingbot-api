# MCP (stdio) adapter for Hummingbot API

This MCP server is a thin stdio adapter. It forwards tool calls to the local Hummingbot API
and does not expose any network port.

## Start order
1. Start Hummingbot API locally (e.g., `make run` or `make deploy`).
2. Start the MCP stdio server:
   - Run from the repo root so `./hummingbot-api-mcp` is available.

```bash
HUMMINGBOT_API_URL=http://127.0.0.1:8000 \
HUMMINGBOT_API_USERNAME=admin \
HUMMINGBOT_API_PASSWORD=admin \
./hummingbot-api-mcp
```

## Environment variables
- `HUMMINGBOT_API_URL` (default: `http://127.0.0.1:8000`)
- `HUMMINGBOT_API_USERNAME` (required)
- `HUMMINGBOT_API_PASSWORD` (required)
- `HUMMINGBOT_API_TIMEOUT_SECONDS` (optional, default: `10`)

## Tools
### Gateway
- `gateway_status`
- `gateway_start`
- `gateway_stop`
- `gateway_restart`
- `gateway_logs`
- `gateway_connectors`
- `gateway_connector_config`
- `gateway_tokens_list`
- `gateway_token_add`
- `gateway_token_delete`
- `gateway_pools_list`
- `gateway_pool_add`
- `gateway_pool_delete`
- `gateway_allowances`
- `gateway_approve`
- `gateway_swap_quote`
- `gateway_swap_execute`
- `gateway_clmm_pool_info`
- `gateway_clmm_pools`
- `gateway_clmm_open`
- `gateway_clmm_close`
- `gateway_clmm_collect_fees`
- `gateway_clmm_positions_owned`
- `gateway_clmm_position_events`
- `gateway_clmm_positions_search`

### Bot orchestration
- `bot_status`
- `bot_instances`
- `bot_start`
- `bot_stop`
- `bot_deploy_v2_script`
- `bot_deploy_v2_controllers`
- `bot_stop_and_archive`

### Controller config
- `controller_configs_list`
- `controller_config_update`

### Script config (global)
- `script_configs_list`
- `script_config_get`
- `script_config_upsert`
- `script_config_delete`
- `script_config_template`

### Controller config (global)
- `controller_configs_list_global`
- `controller_config_get`
- `controller_config_upsert`
- `controller_config_delete`
- `controller_config_template`
- `controller_config_validate`

### Workflow planning (read-only)
- `deploy_v2_workflow_plan`

The workflow planner only reads current state and returns a recommended action list.
It does not execute any mutating calls.

Notes:
- For pool checks, `connector_name` is normalized (e.g., `meteora/clmm` -> `meteora`).
- If `pool_trading_pair` order differs from `trading_pair`, provide `pool_address` or token addresses to avoid ambiguity.
- Allowance checks only apply to EVM chains (e.g., BSC/Uniswap). Solana connectors will skip allowances.

Example input:
```json
{
  "network_id": "ethereum-mainnet",
  "connector_name": "uniswap",
  "pool_type": "clmm",
  "pool_address": "0x...",
  "base": "USDC",
  "quote": "WETH",
  "base_address": "0x...",
  "quote_address": "0x...",
  "fee_pct": 0.3,
  "tokens": [
    {"address": "0x...", "symbol": "USDC", "decimals": 6},
    {"address": "0x...", "symbol": "WETH", "decimals": 18}
  ],
  "wallet_address": "0x...",
  "spender": "uniswap",
  "deployment_type": "controllers",
  "instance_name": "uni-lp-01",
  "credentials_profile": "main",
  "controllers_config": ["my_controller_config"]
}
```

Solana (Meteora) example (planner only):
```json
{
  "network_id": "solana-mainnet-beta",
  "connector_name": "meteora/clmm",
  "pool_type": "clmm",
  "pool_address": "FCL8pjNQsDAggZVczYfnn6tfbYoMnJykGT2cpdTULAxB",
  "base": "PENGU",
  "quote": "SOL",
  "base_address": "GB8KtQfMChhYrCYtd5PoAB42kAdkHnuyAincSSmFpump",
  "quote_address": "So11111111111111111111111111111111111111112",
  "tokens": [
    {"address": "GB8KtQfMChhYrCYtd5PoAB42kAdkHnuyAincSSmFpump", "symbol": "PENGU", "decimals": 6},
    {"address": "So11111111111111111111111111111111111111112", "symbol": "SOL", "decimals": 9}
  ],
  "deployment_type": "controllers",
  "instance_name": "clmm-meteora-pengu-sol",
  "credentials_profile": "main",
  "controllers_config": ["clmm_lp_meteora"]
}
```

BSC (Uniswap) note:
- `trading_pair` and `pool_trading_pair` may be reversed. Use `pool_address` (and token addresses) to make matching deterministic.

Example output (trimmed):
```json
{
  "summary": {"ready": false, "blockers": ["controller_config_missing"], "action_count": 3},
  "checks": [{"name": "gateway_tokens", "status": "ok", "details": {"missing": ["USDC"]}}],
  "actions": [
    {"tool": "gateway_token_add", "arguments": {"network_id": "...", "symbol": "USDC", "decimals": 6}},
    {"tool": "controller_config_upsert", "arguments": {"config_name": "my_controller_config", "config": {}}},
    {"tool": "bot_deploy_v2_controllers", "arguments": {"instance_name": "uni-lp-01", "credentials_profile": "main", "controllers_config": ["my_controller_config"]}}
  ]
}
```

## Claude CLI (stdio)
```bash
claude mcp add --transport stdio hummingbot-api -- \
  env HUMMINGBOT_API_URL=http://127.0.0.1:8000 \
      HUMMINGBOT_API_USERNAME=admin \
      HUMMINGBOT_API_PASSWORD=admin \
      ./hummingbot-api-mcp
```

## Claude Desktop
```json
{
  "mcpServers": {
    "hummingbot-api": {
      "command": "env",
      "args": [
        "HUMMINGBOT_API_URL=http://127.0.0.1:8000",
        "HUMMINGBOT_API_USERNAME=admin",
        "HUMMINGBOT_API_PASSWORD=admin",
        "./hummingbot-api-mcp"
      ]
    }
  }
}
```
