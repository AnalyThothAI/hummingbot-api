# MCP stdio adapter design

## Summary
Provide a local MCP stdio adapter that forwards tool calls to the Hummingbot API.
The adapter is a thin bridge (JSON-RPC over stdio -> HTTP API) and does not expose a network port.

## Goals
- Local-only MCP (stdio) with no network listener.
- Forward selected tools to existing REST endpoints.
- Keep responsibilities separated from API services and business logic.

## Non-goals
- No direct swap/LP tools.
- No Gateway or bot logic changes.
- No external exposure or persistent state.

## Architecture
- `mcp/mcp_server.py` reads stdin JSON-RPC, writes stdout responses.
- `McpHttpClient` wraps local HTTP calls with Basic Auth.
- Tool handlers map to existing endpoints with minimal validation.

## Tool coverage
- Gateway management: status/start/stop/restart/logs
- Gateway config/meta: connectors, connector config, tokens, pools, allowances, approve
- Bot orchestration: status, instances, start, stop, deploy-v2-script, deploy-v2-controllers, stop-and-archive
- Controller config: list configs, update config

## Error handling
- HTTP 4xx/5xx mapped to `isError: true` with status and body text.
- Invalid tool or missing required args return JSON-RPC errors.

## Security
- Basic Auth required via environment variables.
- Default API URL `http://127.0.0.1:8000`.
- No local file reads or service coupling.

## Validation
1. Start API locally.
2. Run `./hummingbot-api-mcp` with env vars.
3. Call `gateway_status`, `bot_instances`, `controller_configs_list` from MCP client.
