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
