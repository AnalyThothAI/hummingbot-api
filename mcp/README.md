# MCP (stdio) adapter for Hummingbot API

This MCP server is a thin stdio adapter. It forwards tool calls to the local Hummingbot API
and does not expose any network port.

## Start order
1. Start Hummingbot API locally (e.g., `make run` or `make deploy`).
2. Start the MCP stdio server:
   - Run from the repo root so `./hummingbot-api-mcp` is available.

```bash
MCP_HUMMINGBOT_API_URL=http://127.0.0.1:18000 \
MCP_HUMMINGBOT_API_USERNAME=admin \
MCP_HUMMINGBOT_API_PASSWORD=admin \
./hummingbot-api-mcp
```

If you run the API directly with uvicorn (not docker-compose), use `http://127.0.0.1:8000`.

## Environment variables
- `MCP_HUMMINGBOT_API_URL` (default: `http://127.0.0.1:8000`)
- `MCP_HUMMINGBOT_API_USERNAME` (required)
- `MCP_HUMMINGBOT_API_PASSWORD` (required)
- `MCP_HUMMINGBOT_API_TIMEOUT_SECONDS` (optional, default: `10`)

The MCP process reads `.env` automatically if present (simple loader, no extra deps).
`make run` starts the API, but does not start MCP.

### Port note (docker-compose vs uvicorn)
- `make deploy` uses docker-compose and maps the API to `http://127.0.0.1:18000` on the host.
- `make run` starts uvicorn directly and defaults to `http://127.0.0.1:8000`.

## Tools
### Gateway
- `gateway_status`
- `gateway_start`
- `gateway_stop`
- `gateway_restart`
- `gateway_logs`
- `gateway_connectors`
- `gateway_connector_config`
- `gateway_connector_config_update`
- `gateway_wallets_list`
- `gateway_wallet_create`
- `gateway_chains`
- `gateway_networks`
- `gateway_network_config_get`
- `gateway_network_config_update`
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
- `gateway_swaps_status`
- `gateway_swaps_search`
- `gateway_swaps_summary`
- `gateway_clmm_pool_info`
- `gateway_clmm_pools`
- `gateway_clmm_open`
- `gateway_clmm_close`
- `gateway_clmm_collect_fees`
- `gateway_clmm_positions_owned`
- `gateway_clmm_position_events`
- `gateway_clmm_positions_search`

### Swap (unified trading/swap)
`gateway_swap_quote` and `gateway_swap_execute` now align with Gateway `/trading/swap`.
Inputs use the unified fields:
- `chainNetwork` (e.g., `ethereum-bsc`, `solana-mainnet-beta`)
- `baseToken`, `quoteToken` (symbol or address)
- `amount`, `side` (`BUY`/`SELL`)
- Optional: `connector` (e.g., `uniswap/router`, `pancakeswap/router`, `jupiter/router`), `slippagePct`, `walletAddress`
  - `slippagePct` is a percentage (0-100). `1` = 1%, `0.01` = 0.01% (1 bp).
  - If `connector` is omitted, Gateway uses the network's configured `swap_provider` (see `gateway_network_config_get`).
  - Typical defaults in this repo:
    - `solana-mainnet-beta` -> `jupiter/router`
    - `ethereum-base` -> `uniswap/router`
    - `ethereum-bsc` -> `uniswap/router`
    - `pancakeswap/router` is still supported on BSC as an explicit override.

Important: Do not copy Gateway `slippagePct` values directly into controller YAML `*_pct` fields.
Controller configs use **ratio** semantics for `*_pct` fields: `0.01` means 1% (and `0.05` means 5%).
Percent-points values like `5`/`30` are intentionally rejected to avoid ambiguity.

Example (quote):
```json
{
  "chainNetwork": "ethereum-bsc",
  "baseToken": "0x987e6269c6b7ea6898221882f11ea16f87b97777",
  "quoteToken": "0x55d398326f99059ff775485246999027b3197955",
  "amount": 1,
  "side": "SELL",
  "connector": "uniswap/router",
  "slippagePct": 1
}
```

Example (execute):
```json
{
  "chainNetwork": "ethereum-bsc",
  "baseToken": "0x987e6269c6b7ea6898221882f11ea16f87b97777",
  "quoteToken": "0x55d398326f99059ff775485246999027b3197955",
  "amount": 1,
  "side": "SELL",
  "connector": "uniswap/router",
  "walletAddress": "0xYourWallet",
  "slippagePct": 1
}
```

Allowance/authorization (EVM only):
- Check: `gateway_allowances` with `{network_id, address, tokens, spender}`
- Approve: `gateway_approve` for missing token+spender before `gateway_swap_execute`
  - `spender` should be a connector string with a suffix (contains `/`), e.g. `pancakeswap/router`, `uniswap/router`, `uniswap/clmm`, or a direct spender address (do not strip the suffix).

Gas / transaction settings (Gateway-level):
- Swap tools in this MCP adapter do **not** accept gas parameters directly.
- Tune per-network settings via:
  - `gateway_network_config_get`
  - `gateway_network_config_update` (mutating; requires care)
- Tune per-connector settings via:
  - `gateway_connector_config`
  - `gateway_connector_config_update`

Common defaults (from Gateway templates; verify actual runtime config via `gateway_network_config_get`):
- Base (`ethereum-base`): EIP-1559 style (`baseFeeMultiplier: 1.2`, `priorityFee: 0.001` gwei).
- BSC (`ethereum-bsc`): legacy `gasPrice` (often left blank to auto-fetch from RPC).
- Solana (`solana-mainnet-beta`): `defaultComputeUnits`, `confirmRetryInterval`, `confirmRetryCount`, `minPriorityFeePerCU`.

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

## Skills
Recommended local skill in this repo:
- `skills/mcp-bot-ops` for plan-first deploy-v2, unified swap, and lifecycle ops

Notes:
- For pool checks, `connector_name` is normalized (e.g., `meteora/clmm` -> `meteora`).
- Uniswap pools can have token0/token1 ordering opposite to your trading pair; planner treats this as a valid match.
- For other connectors (e.g., Meteora), pool order is not reversed.
- If `pool_trading_pair` order differs from `trading_pair`, provide `pool_address` or token addresses to avoid ambiguity.
- CLMM LP controllers default `rebalance_enabled: false` in config. Set it explicitly if you want rebalance.
- Some CLMM LP controllers support an entry gate via `target_price` + `trigger_above`. Unless you explicitly want a “wait for price” behavior, use `target_price: 0` to avoid an instance that looks running but never opens.
- Allowance checks only apply to EVM chains (`chain == ethereum`). Solana connectors will skip allowances.
- If tokens/pools are missing, the plan will include `gateway_restart` and **block deploy** until you restart and re-run the plan.
- If `network_id` is omitted but `gateway_network_id` is provided, the planner uses it for Gateway checks.
- Controller configs should include `id` and it is recommended to set `id == YAML basename` (without `.yml`) so bot reports map cleanly to dashboard units.

## Agent usage (recommended)
This MCP module is intended for **plan-first** automation. Agents should build a plan, then execute actions step-by-step.

### 1) Collect minimal inputs
- `network_id` (chain-network, e.g., `solana-mainnet-beta` / `ethereum-bsc`)
- `connector_name` (e.g., `meteora/clmm`, `uniswap/clmm`)
- Prefer `pool_address` for deterministic matching
- Token info: at least addresses; use `metadata_token` when symbol/decimals are missing
- Deployment info: `deployment_type`, `instance_name`, `credentials_profile`,
  and either `controllers_config` or `script` + `script_config`

### 2) Fill missing token metadata
- Tool: `metadata_token` (Gateway/Gecko-backed)

### 2.5) Approvals (Deploy V2 UI logic)
- Tokens = base+quote from `trading_pair`
- Spenders = `connector_name` (gateway) + `router_connector` (when `exit_full_liquidation` is true)
- Check: `gateway_allowances` with `{network_id, address: wallet_address, tokens, spender}`
- Treat allowance >= 1e10 as "Unlimited"; if not, call `gateway_approve` for each missing token+spender

### 3) Build a read-only plan
- Tool: `deploy_v2_workflow_plan`
- Output: `summary`, `checks`, `actions`, `blockers`, `notes`

### 4) Execute the plan (optional, step-by-step)
- Run each `actions[]` tool in order.
- When `gateway_token_add` / `gateway_pool_add` appears, **restart Gateway** using `gateway_restart`, then **re-run the planner**.
- Deploy actions will only appear after the restart+replan step.

### 5) Re-run the planner to validate
- If `summary.ready` is true and `blockers` is empty, the chain is consistent.

## Validation checklist (manual)
- Gateway running: `gateway_status`
- Token exists: `gateway_tokens_list`
- Pool exists: `gateway_pools_list`
- Allowance (EVM only): `gateway_allowances`
- Controller config exists: `controller_config_get`
- Script config exists: `script_config_get`
- Instance exists: `bot_instances`

## Pool lookup limits (important)
- `metadata_pools` uses GeckoTerminal via Gateway.
- **Meteora** has a connector fallback in API; **Uniswap does not**.
- If Gecko is rate-limited, provide `pool_address` or rely on saved pools in `/gateway/pools`.
- If `metadata_token` cannot resolve symbol/decimals, the agent must supply them explicitly.

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
  env MCP_HUMMINGBOT_API_URL=http://127.0.0.1:18000 \
      MCP_HUMMINGBOT_API_USERNAME=admin \
      MCP_HUMMINGBOT_API_PASSWORD=admin \
      ./hummingbot-api-mcp
```

## Claude Desktop
```json
{
  "mcpServers": {
    "hummingbot-api": {
      "command": "env",
      "args": [
        "MCP_HUMMINGBOT_API_URL=http://127.0.0.1:18000",
        "MCP_HUMMINGBOT_API_USERNAME=admin",
        "MCP_HUMMINGBOT_API_PASSWORD=admin",
        "./hummingbot-api-mcp"
      ]
    }
  }
}
```

## Docker (optional)
If you want a dedicated MCP image:

Build:
```bash
docker build -t hummingbot-api-mcp:local -f mcp/Dockerfile .
```

Run (macOS/Windows):
```bash
docker run --rm -i \
  -e MCP_HUMMINGBOT_API_URL=http://host.docker.internal:18000 \
  -e MCP_HUMMINGBOT_API_USERNAME=admin \
  -e MCP_HUMMINGBOT_API_PASSWORD=admin \
  hummingbot-api-mcp:local
```

Run (Linux):
```bash
docker run --rm -i --network host \
  -e MCP_HUMMINGBOT_API_URL=http://127.0.0.1:18000 \
  -e MCP_HUMMINGBOT_API_USERNAME=admin \
  -e MCP_HUMMINGBOT_API_PASSWORD=admin \
  hummingbot-api-mcp:local
```

If you prefer not to build an image, you can run MCP in a container without creating one:

macOS/Windows (use host.docker.internal):
```bash
docker run --rm -i -v "$PWD:/app" -w /app \
  -e MCP_HUMMINGBOT_API_URL=http://host.docker.internal:18000 \
  -e MCP_HUMMINGBOT_API_USERNAME=admin \
  -e MCP_HUMMINGBOT_API_PASSWORD=admin \
  python:3.12-slim bash -lc "pip install -q httpx && ./hummingbot-api-mcp"
```

`make mcp-docker` behavior:
- If `.env` exists, it is mounted into the container as `/app/.env` and read by MCP.
- If `MCP_HUMMINGBOT_API_*` variables are set in your shell, they are passed explicitly.
- If neither is provided, the container will not have credentials and MCP will exit.
- Runs **detached** by default (non-blocking). Stop it with `make mcp-docker-stop`.

Linux (use host network):
```bash
docker run --rm -i --network host -v "$PWD:/app" -w /app \
  -e MCP_HUMMINGBOT_API_URL=http://127.0.0.1:18000 \
  -e MCP_HUMMINGBOT_API_USERNAME=admin \
  -e MCP_HUMMINGBOT_API_PASSWORD=admin \
  python:3.12-slim bash -lc "pip install -q httpx && ./hummingbot-api-mcp"
```

If you run the API directly with uvicorn (not docker-compose), replace `18000` with `8000`.
