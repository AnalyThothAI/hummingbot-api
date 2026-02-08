"""Unified tool registry for MCP adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.http_client import McpHttpClient
else:
    McpHttpClient = Any


class UnknownToolError(Exception):
    """Raised when a requested tool name is not registered (protocol-level error)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[dict, McpHttpClient], Any]


def _pick_params(arguments: dict, keys: Iterable[str]) -> dict:
    payload = {}
    for key in keys:
        if key in arguments and arguments[key] is not None:
            payload[key] = arguments[key]
    return payload


def _gateway_status(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/gateway/status")


def _gateway_start(arguments: dict, http_client: McpHttpClient) -> Any:
    passphrase = arguments.get("passphrase")
    if not passphrase:
        raise ValueError("passphrase is required")
    return http_client.post("/gateway/start", json_body=arguments)


def _gateway_stop(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.post("/gateway/stop")


def _gateway_restart(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = arguments or None
    return http_client.post("/gateway/restart", json_body=payload)


def _gateway_logs(arguments: dict, http_client: McpHttpClient) -> Any:
    params = _pick_params(arguments, ["tail"])
    return http_client.get("/gateway/logs", params=params)


def _gateway_connectors(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/gateway/connectors")


def _gateway_connector_config(arguments: dict, http_client: McpHttpClient) -> Any:
    connector_name = arguments.get("connector_name")
    if not connector_name:
        raise ValueError("connector_name is required")
    return http_client.get(f"/gateway/connectors/{connector_name}")


def _gateway_connector_config_update(arguments: dict, http_client: McpHttpClient) -> Any:
    connector_name = arguments.get("connector_name")
    config_updates = arguments.get("config_updates")
    if not connector_name:
        raise ValueError("connector_name is required")
    if not isinstance(config_updates, dict):
        raise ValueError("config_updates must be an object")
    return http_client.post(f"/gateway/connectors/{connector_name}", json_body=config_updates)


def _gateway_chains(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/gateway/chains")


def _gateway_networks(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/gateway/networks")


def _gateway_network_config_get(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    if not network_id:
        raise ValueError("network_id is required")
    return http_client.get(f"/gateway/networks/{network_id}")


def _gateway_network_config_update(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    config_updates = arguments.get("config_updates")
    if not network_id:
        raise ValueError("network_id is required")
    if not isinstance(config_updates, dict):
        raise ValueError("config_updates must be an object")
    return http_client.post(f"/gateway/networks/{network_id}", json_body=config_updates)


def _gateway_wallets_list(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/accounts/gateway/wallets")


def _gateway_wallet_create(arguments: dict, http_client: McpHttpClient) -> Any:
    chain = arguments.get("chain")
    if not chain:
        raise ValueError("chain is required")
    payload = _pick_params(arguments, ["chain", "set_default"])
    return http_client.post("/gateway/wallets/create", json_body=payload)


def _gateway_tokens_list(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    if not network_id:
        raise ValueError("network_id is required")
    params = _pick_params(arguments, ["search"])
    return http_client.get(f"/gateway/networks/{network_id}/tokens", params=params)


def _gateway_token_add(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    if not network_id:
        raise ValueError("network_id is required")
    payload = _pick_params(arguments, ["address", "symbol", "name", "decimals"])
    if "address" not in payload:
        raise ValueError("address is required")
    if "symbol" not in payload:
        raise ValueError("symbol is required")
    if "decimals" not in payload:
        raise ValueError("decimals is required")
    return http_client.post(f"/gateway/networks/{network_id}/tokens", json_body=payload)


def _gateway_token_delete(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    token_address = arguments.get("token_address")
    if not network_id:
        raise ValueError("network_id is required")
    if not token_address:
        raise ValueError("token_address is required")
    return http_client.delete(f"/gateway/networks/{network_id}/tokens/{token_address}")


def _gateway_pools_list(arguments: dict, http_client: McpHttpClient) -> Any:
    connector_name = arguments.get("connector_name")
    if not connector_name:
        raise ValueError("connector_name is required")
    params = _pick_params(arguments, ["connector_name", "network", "pool_type", "search"])
    return http_client.get("/gateway/pools", params=params)


def _gateway_pool_add(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        [
            "connector_name",
            "type",
            "network",
            "address",
            "base",
            "quote",
            "base_address",
            "quote_address",
            "fee_pct",
        ],
    )
    for key in ("connector_name", "type", "network", "address", "base", "quote", "base_address", "quote_address"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/gateway/pools", json_body=payload)


def _gateway_pool_delete(arguments: dict, http_client: McpHttpClient) -> Any:
    address = arguments.get("address")
    connector_name = arguments.get("connector_name")
    network = arguments.get("network")
    pool_type = arguments.get("pool_type")
    if not address:
        raise ValueError("address is required")
    if not connector_name:
        raise ValueError("connector_name is required")
    if not network:
        raise ValueError("network is required")
    if not pool_type:
        raise ValueError("pool_type is required")
    params = {"connector_name": connector_name, "network": network, "pool_type": pool_type}
    return http_client.delete(f"/gateway/pools/{address}", params=params)


def _gateway_allowances(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(arguments, ["network_id", "chain", "network", "address", "tokens", "spender"])
    if "address" not in payload:
        raise ValueError("address is required")
    if "tokens" not in payload:
        raise ValueError("tokens is required")
    if "spender" not in payload:
        raise ValueError("spender is required")
    return http_client.post("/gateway/allowances", json_body=payload)


def _gateway_approve(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        ["network_id", "chain", "network", "address", "token", "spender", "amount"],
    )
    if "address" not in payload:
        raise ValueError("address is required")
    if "token" not in payload:
        raise ValueError("token is required")
    if "spender" not in payload:
        raise ValueError("spender is required")
    return http_client.post("/gateway/approve", json_body=payload)


def _gateway_swap_quote(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        ["chainNetwork", "connector", "baseToken", "quoteToken", "amount", "side", "slippagePct"],
    )
    for key in ("chainNetwork", "baseToken", "quoteToken", "amount", "side"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.get("/gateway/trading/swap/quote", params=payload)


def _gateway_swap_execute(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        [
            "chainNetwork",
            "connector",
            "baseToken",
            "quoteToken",
            "amount",
            "side",
            "slippagePct",
            "walletAddress",
        ],
    )
    for key in ("chainNetwork", "baseToken", "quoteToken", "amount", "side"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/gateway/trading/swap/execute", json_body=payload)


def _gateway_swaps_status(arguments: dict, http_client: McpHttpClient) -> Any:
    transaction_hash = arguments.get("transaction_hash")
    if not transaction_hash:
        raise ValueError("transaction_hash is required")
    return http_client.get(f"/gateway/swaps/{transaction_hash}/status")


def _gateway_swaps_search(arguments: dict, http_client: McpHttpClient) -> Any:
    params = _pick_params(
        arguments,
        [
            "network",
            "connector",
            "wallet_address",
            "trading_pair",
            "status",
            "start_time",
            "end_time",
            "limit",
            "offset",
        ],
    )
    return http_client.post("/gateway/swaps/search", params=params)


def _gateway_swaps_summary(arguments: dict, http_client: McpHttpClient) -> Any:
    params = _pick_params(arguments, ["network", "wallet_address", "start_time", "end_time"])
    return http_client.get("/gateway/swaps/summary", params=params)


def _gateway_clmm_pool_info(arguments: dict, http_client: McpHttpClient) -> Any:
    connector = arguments.get("connector")
    network = arguments.get("network")
    pool_address = arguments.get("pool_address")
    if not connector:
        raise ValueError("connector is required")
    if not network:
        raise ValueError("network is required")
    if not pool_address:
        raise ValueError("pool_address is required")
    params = {"connector": connector, "network": network, "pool_address": pool_address}
    return http_client.get("/gateway/clmm/pool-info", params=params)


def _gateway_clmm_pools(arguments: dict, http_client: McpHttpClient) -> Any:
    connector = arguments.get("connector")
    if not connector:
        raise ValueError("connector is required")
    params = _pick_params(arguments, ["connector", "page", "limit", "search_term", "sort_key", "order_by", "include_unknown"])
    return http_client.get("/gateway/clmm/pools", params=params)


def _gateway_clmm_open(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        [
            "connector",
            "network",
            "pool_address",
            "lower_price",
            "upper_price",
            "base_token_amount",
            "quote_token_amount",
            "slippage_pct",
            "wallet_address",
            "extra_params",
        ],
    )
    for key in ("connector", "network", "pool_address", "lower_price", "upper_price"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/gateway/clmm/open", json_body=payload)


def _gateway_clmm_close(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(arguments, ["connector", "network", "position_address", "wallet_address"])
    for key in ("connector", "network", "position_address"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/gateway/clmm/close", json_body=payload)


def _gateway_clmm_collect_fees(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(arguments, ["connector", "network", "position_address", "wallet_address"])
    for key in ("connector", "network", "position_address"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/gateway/clmm/collect-fees", json_body=payload)


def _gateway_clmm_positions_owned(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(arguments, ["connector", "network", "pool_address", "wallet_address"])
    for key in ("connector", "network", "pool_address"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/gateway/clmm/positions_owned", json_body=payload)


def _gateway_clmm_position_events(arguments: dict, http_client: McpHttpClient) -> Any:
    position_address = arguments.get("position_address")
    if not position_address:
        raise ValueError("position_address is required")
    params = _pick_params(arguments, ["event_type", "limit"])
    return http_client.get(f"/gateway/clmm/positions/{position_address}/events", params=params)


def _gateway_clmm_positions_search(arguments: dict, http_client: McpHttpClient) -> Any:
    params = _pick_params(
        arguments,
        [
            "network",
            "connector",
            "wallet_address",
            "trading_pair",
            "status",
            "position_addresses",
            "limit",
            "offset",
            "refresh",
        ],
    )
    return http_client.post("/gateway/clmm/positions/search", params=params)


def _bot_status(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/bot-orchestration/status")


def _bot_instances(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/bot-orchestration/instances")


def _bot_start(arguments: dict, http_client: McpHttpClient) -> Any:
    bot_name = arguments.get("bot_name")
    if not bot_name:
        raise ValueError("bot_name is required")
    payload = _pick_params(arguments, ["bot_name", "log_level", "script", "conf", "async_backend"])
    return http_client.post("/bot-orchestration/start-bot", json_body=payload)


def _bot_stop(arguments: dict, http_client: McpHttpClient) -> Any:
    bot_name = arguments.get("bot_name")
    if not bot_name:
        raise ValueError("bot_name is required")
    payload = _pick_params(arguments, ["bot_name", "skip_order_cancellation", "async_backend"])
    return http_client.post("/bot-orchestration/stop-bot", json_body=payload)


def _bot_deploy_v2_script(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        [
            "instance_name",
            "credentials_profile",
            "image",
            "script",
            "script_config",
            "gateway_network_id",
            "gateway_wallet_address",
            "apply_gateway_defaults",
            "headless",
        ],
    )
    for key in ("instance_name", "credentials_profile"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/bot-orchestration/deploy-v2-script", json_body=payload)


def _bot_deploy_v2_controllers(arguments: dict, http_client: McpHttpClient) -> Any:
    payload = _pick_params(
        arguments,
        [
            "instance_name",
            "credentials_profile",
            "controllers_config",
            "max_global_drawdown_quote",
            "max_controller_drawdown_quote",
            "gateway_network_id",
            "gateway_wallet_address",
            "apply_gateway_defaults",
            "unique_instance_name",
            "image",
            "headless",
        ],
    )
    for key in ("instance_name", "credentials_profile", "controllers_config"):
        if key not in payload:
            raise ValueError(f"{key} is required")
    return http_client.post("/bot-orchestration/deploy-v2-controllers", json_body=payload)


def _bot_stop_and_archive(arguments: dict, http_client: McpHttpClient) -> Any:
    bot_name = arguments.get("bot_name")
    if not bot_name:
        raise ValueError("bot_name is required")
    params = _pick_params(arguments, ["skip_order_cancellation", "archive_locally", "s3_bucket"])
    return http_client.post(f"/bot-orchestration/stop-and-archive-bot/{bot_name}", params=params)


def _controller_configs_list(arguments: dict, http_client: McpHttpClient) -> Any:
    bot_name = arguments.get("bot_name")
    if not bot_name:
        raise ValueError("bot_name is required")
    return http_client.get(f"/controllers/bots/{bot_name}/configs")


def _controller_config_update(arguments: dict, http_client: McpHttpClient) -> Any:
    bot_name = arguments.get("bot_name")
    controller_name = arguments.get("controller_name")
    config = arguments.get("config")
    if not bot_name:
        raise ValueError("bot_name is required")
    if not controller_name:
        raise ValueError("controller_name is required")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    return http_client.post(
        f"/controllers/bots/{bot_name}/{controller_name}/config",
        json_body=config,
    )


def _script_configs_list(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/scripts/configs")


def _script_config_get(arguments: dict, http_client: McpHttpClient) -> Any:
    config_name = arguments.get("config_name")
    if not config_name:
        raise ValueError("config_name is required")
    return http_client.get(f"/scripts/configs/{config_name}")


def _script_config_upsert(arguments: dict, http_client: McpHttpClient) -> Any:
    config_name = arguments.get("config_name")
    config = arguments.get("config")
    if not config_name:
        raise ValueError("config_name is required")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    return http_client.post(f"/scripts/configs/{config_name}", json_body=config)


def _script_config_delete(arguments: dict, http_client: McpHttpClient) -> Any:
    config_name = arguments.get("config_name")
    if not config_name:
        raise ValueError("config_name is required")
    return http_client.delete(f"/scripts/configs/{config_name}")


def _script_config_template(arguments: dict, http_client: McpHttpClient) -> Any:
    script_name = arguments.get("script_name")
    if not script_name:
        raise ValueError("script_name is required")
    return http_client.get(f"/scripts/{script_name}/config/template")


def _controller_configs_list_global(arguments: dict, http_client: McpHttpClient) -> Any:
    return http_client.get("/controllers/configs")


def _controller_config_get(arguments: dict, http_client: McpHttpClient) -> Any:
    config_name = arguments.get("config_name")
    if not config_name:
        raise ValueError("config_name is required")
    return http_client.get(f"/controllers/configs/{config_name}")


def _controller_config_upsert(arguments: dict, http_client: McpHttpClient) -> Any:
    config_name = arguments.get("config_name")
    config = arguments.get("config")
    if not config_name:
        raise ValueError("config_name is required")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    return http_client.post(f"/controllers/configs/{config_name}", json_body=config)


def _controller_config_delete(arguments: dict, http_client: McpHttpClient) -> Any:
    config_name = arguments.get("config_name")
    if not config_name:
        raise ValueError("config_name is required")
    return http_client.delete(f"/controllers/configs/{config_name}")


def _controller_config_template(arguments: dict, http_client: McpHttpClient) -> Any:
    controller_type = arguments.get("controller_type")
    controller_name = arguments.get("controller_name")
    if not controller_type:
        raise ValueError("controller_type is required")
    if not controller_name:
        raise ValueError("controller_name is required")
    return http_client.get(f"/controllers/{controller_type}/{controller_name}/config/template")


def _controller_config_validate(arguments: dict, http_client: McpHttpClient) -> Any:
    controller_type = arguments.get("controller_type")
    controller_name = arguments.get("controller_name")
    config = arguments.get("config")
    if not controller_type:
        raise ValueError("controller_type is required")
    if not controller_name:
        raise ValueError("controller_name is required")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    return http_client.post(
        f"/controllers/{controller_type}/{controller_name}/config/validate",
        json_body=config,
    )


def _deploy_v2_workflow_plan(arguments: dict, http_client: McpHttpClient) -> Any:
    from mcp.workflows import build_deploy_v2_workflow_plan

    return build_deploy_v2_workflow_plan(arguments, http_client)


def _metadata_token(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    address = arguments.get("address")
    if not network_id:
        raise ValueError("network_id is required")
    if not address:
        raise ValueError("address is required")
    return http_client.get("/metadata/token", params={"network_id": network_id, "address": address})


def _metadata_pools(arguments: dict, http_client: McpHttpClient) -> Any:
    network_id = arguments.get("network_id")
    if not network_id:
        raise ValueError("network_id is required")
    params = _pick_params(
        arguments,
        ["network_id", "connector", "pool_type", "token_a", "token_b", "search", "pages", "limit"],
    )
    return http_client.get("/metadata/pools", params=params)


_TOOL_SPECS: List[ToolSpec] = [
    ToolSpec(
        name="gateway_status",
        description="Get Gateway container status.",
        input_schema={"type": "object", "properties": {}},
        handler=_gateway_status,
    ),
    ToolSpec(
        name="gateway_start",
        description="Start Gateway container.",
        input_schema={
            "type": "object",
            "properties": {
                "passphrase": {"type": "string"},
                "image": {"type": "string"},
                "port": {"type": "integer"},
                "dev_mode": {"type": "boolean"},
            },
            "required": ["passphrase"],
        },
        handler=_gateway_start,
    ),
    ToolSpec(
        name="gateway_stop",
        description="Stop Gateway container.",
        input_schema={"type": "object", "properties": {}},
        handler=_gateway_stop,
    ),
    ToolSpec(
        name="gateway_restart",
        description="Restart Gateway container (optional config).",
        input_schema={
            "type": "object",
            "properties": {
                "passphrase": {"type": "string"},
                "image": {"type": "string"},
                "port": {"type": "integer"},
                "dev_mode": {"type": "boolean"},
            },
        },
        handler=_gateway_restart,
    ),
    ToolSpec(
        name="gateway_logs",
        description="Get Gateway container logs.",
        input_schema={
            "type": "object",
            "properties": {"tail": {"type": "integer"}},
        },
        handler=_gateway_logs,
    ),
    ToolSpec(
        name="gateway_connectors",
        description="List available DEX connectors from Gateway.",
        input_schema={"type": "object", "properties": {}},
        handler=_gateway_connectors,
    ),
    ToolSpec(
        name="gateway_connector_config",
        description="Get Gateway connector configuration.",
        input_schema={
            "type": "object",
            "properties": {"connector_name": {"type": "string"}},
            "required": ["connector_name"],
        },
        handler=_gateway_connector_config,
    ),
    ToolSpec(
        name="gateway_connector_config_update",
        description="Update Gateway connector configuration.",
        input_schema={
            "type": "object",
            "properties": {
                "connector_name": {"type": "string"},
                "config_updates": {"type": "object", "additionalProperties": True},
            },
            "required": ["connector_name", "config_updates"],
        },
        handler=_gateway_connector_config_update,
    ),
    ToolSpec(
        name="gateway_chains",
        description="List available Gateway chains and networks.",
        input_schema={"type": "object", "properties": {}},
        handler=_gateway_chains,
    ),
    ToolSpec(
        name="gateway_networks",
        description="List available Gateway networks.",
        input_schema={"type": "object", "properties": {}},
        handler=_gateway_networks,
    ),
    ToolSpec(
        name="gateway_network_config_get",
        description="Get Gateway network configuration.",
        input_schema={
            "type": "object",
            "properties": {"network_id": {"type": "string"}},
            "required": ["network_id"],
        },
        handler=_gateway_network_config_get,
    ),
    ToolSpec(
        name="gateway_network_config_update",
        description="Update Gateway network configuration.",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "config_updates": {"type": "object", "additionalProperties": True},
            },
            "required": ["network_id", "config_updates"],
        },
        handler=_gateway_network_config_update,
    ),
    ToolSpec(
        name="gateway_wallets_list",
        description="List Gateway wallets (includes default wallet flags).",
        input_schema={"type": "object", "properties": {}},
        handler=_gateway_wallets_list,
    ),
    ToolSpec(
        name="gateway_wallet_create",
        description="Create a new Gateway wallet (no private key required).",
        input_schema={
            "type": "object",
            "properties": {
                "chain": {"type": "string"},
                "set_default": {"type": "boolean"},
            },
            "required": ["chain"],
        },
        handler=_gateway_wallet_create,
    ),
    ToolSpec(
        name="gateway_tokens_list",
        description="List tokens for a Gateway network.",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "search": {"type": "string"},
            },
            "required": ["network_id"],
        },
        handler=_gateway_tokens_list,
    ),
    ToolSpec(
        name="gateway_token_add",
        description="Add a custom token for a Gateway network.",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "address": {"type": "string"},
                "symbol": {"type": "string"},
                "name": {"type": "string"},
                "decimals": {"type": "integer"},
            },
            "required": ["network_id", "address", "symbol", "decimals"],
        },
        handler=_gateway_token_add,
    ),
    ToolSpec(
        name="gateway_token_delete",
        description="Delete a custom token from a Gateway network.",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "token_address": {"type": "string"},
            },
            "required": ["network_id", "token_address"],
        },
        handler=_gateway_token_delete,
    ),
    ToolSpec(
        name="gateway_pools_list",
        description="List pools from Gateway.",
        input_schema={
            "type": "object",
            "properties": {
                "connector_name": {"type": "string"},
                "network": {"type": "string"},
                "pool_type": {"type": "string"},
                "search": {"type": "string"},
            },
            "required": ["connector_name"],
        },
        handler=_gateway_pools_list,
    ),
    ToolSpec(
        name="gateway_pool_add",
        description="Add a custom pool to Gateway.",
        input_schema={
            "type": "object",
            "properties": {
                "connector_name": {"type": "string"},
                "type": {"type": "string"},
                "network": {"type": "string"},
                "address": {"type": "string"},
                "base": {"type": "string"},
                "quote": {"type": "string"},
                "base_address": {"type": "string"},
                "quote_address": {"type": "string"},
                "fee_pct": {"type": "number"},
            },
            "required": [
                "connector_name",
                "type",
                "network",
                "address",
                "base",
                "quote",
                "base_address",
                "quote_address",
            ],
        },
        handler=_gateway_pool_add,
    ),
    ToolSpec(
        name="gateway_pool_delete",
        description="Delete a pool from Gateway.",
        input_schema={
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "connector_name": {"type": "string"},
                "network": {"type": "string"},
                "pool_type": {"type": "string"},
            },
            "required": ["address", "connector_name", "network", "pool_type"],
        },
        handler=_gateway_pool_delete,
    ),
    ToolSpec(
        name="gateway_allowances",
        description="Get ERC20 token allowances via Gateway.",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "chain": {"type": "string"},
                "network": {"type": "string"},
                "address": {"type": "string"},
                "tokens": {"type": "array", "items": {"type": "string"}},
                "spender": {"type": "string"},
            },
            "required": ["address", "tokens", "spender"],
        },
        handler=_gateway_allowances,
    ),
    ToolSpec(
        name="gateway_approve",
        description="Approve ERC20 token spending via Gateway.",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "chain": {"type": "string"},
                "network": {"type": "string"},
                "address": {"type": "string"},
                "token": {"type": "string"},
                "spender": {"type": "string"},
                "amount": {"type": "string"},
            },
            "required": ["address", "token", "spender"],
        },
        handler=_gateway_approve,
    ),
    ToolSpec(
        name="gateway_swap_quote",
        description="Get a swap quote via Gateway trading/swap (unified).",
        input_schema={
            "type": "object",
            "properties": {
                "chainNetwork": {"type": "string"},
                "connector": {"type": "string"},
                "baseToken": {"type": "string"},
                "quoteToken": {"type": "string"},
                "side": {"type": "string"},
                "amount": {"type": "number"},
                "slippagePct": {"type": "number"},
            },
            "required": ["chainNetwork", "baseToken", "quoteToken", "side", "amount"],
        },
        handler=_gateway_swap_quote,
    ),
    ToolSpec(
        name="gateway_swap_execute",
        description="Execute a swap via Gateway trading/swap (unified).",
        input_schema={
            "type": "object",
            "properties": {
                "chainNetwork": {"type": "string"},
                "connector": {"type": "string"},
                "baseToken": {"type": "string"},
                "quoteToken": {"type": "string"},
                "side": {"type": "string"},
                "amount": {"type": "number"},
                "slippagePct": {"type": "number"},
                "walletAddress": {"type": "string"},
            },
            "required": ["chainNetwork", "baseToken", "quoteToken", "side", "amount"],
        },
        handler=_gateway_swap_execute,
    ),
    ToolSpec(
        name="gateway_swaps_status",
        description="Get swap status by transaction hash.",
        input_schema={
            "type": "object",
            "properties": {"transaction_hash": {"type": "string"}},
            "required": ["transaction_hash"],
        },
        handler=_gateway_swaps_status,
    ),
    ToolSpec(
        name="gateway_swaps_search",
        description="Search swap history with filters.",
        input_schema={
            "type": "object",
            "properties": {
                "network": {"type": "string"},
                "connector": {"type": "string"},
                "wallet_address": {"type": "string"},
                "trading_pair": {"type": "string"},
                "status": {"type": "string"},
                "start_time": {"type": "integer"},
                "end_time": {"type": "integer"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
        },
        handler=_gateway_swaps_search,
    ),
    ToolSpec(
        name="gateway_swaps_summary",
        description="Get swap summary statistics.",
        input_schema={
            "type": "object",
            "properties": {
                "network": {"type": "string"},
                "wallet_address": {"type": "string"},
                "start_time": {"type": "integer"},
                "end_time": {"type": "integer"},
            },
        },
        handler=_gateway_swaps_summary,
    ),
    ToolSpec(
        name="gateway_clmm_pool_info",
        description="Get CLMM pool info.",
        input_schema={
            "type": "object",
            "properties": {
                "connector": {"type": "string"},
                "network": {"type": "string"},
                "pool_address": {"type": "string"},
            },
            "required": ["connector", "network", "pool_address"],
        },
        handler=_gateway_clmm_pool_info,
    ),
    ToolSpec(
        name="gateway_clmm_pools",
        description="List CLMM pools for a connector.",
        input_schema={
            "type": "object",
            "properties": {
                "connector": {"type": "string"},
                "page": {"type": "integer"},
                "limit": {"type": "integer"},
                "search_term": {"type": "string"},
                "sort_key": {"type": "string"},
                "order_by": {"type": "string"},
                "include_unknown": {"type": "boolean"},
            },
            "required": ["connector"],
        },
        handler=_gateway_clmm_pools,
    ),
    ToolSpec(
        name="gateway_clmm_open",
        description="Open a CLMM position.",
        input_schema={
            "type": "object",
            "properties": {
                "connector": {"type": "string"},
                "network": {"type": "string"},
                "pool_address": {"type": "string"},
                "lower_price": {"type": "number"},
                "upper_price": {"type": "number"},
                "base_token_amount": {"type": "number"},
                "quote_token_amount": {"type": "number"},
                "slippage_pct": {"type": "number"},
                "wallet_address": {"type": "string"},
                "extra_params": {"type": "object", "additionalProperties": True},
            },
            "required": ["connector", "network", "pool_address", "lower_price", "upper_price"],
        },
        handler=_gateway_clmm_open,
    ),
    ToolSpec(
        name="gateway_clmm_close",
        description="Close a CLMM position.",
        input_schema={
            "type": "object",
            "properties": {
                "connector": {"type": "string"},
                "network": {"type": "string"},
                "position_address": {"type": "string"},
                "wallet_address": {"type": "string"},
            },
            "required": ["connector", "network", "position_address"],
        },
        handler=_gateway_clmm_close,
    ),
    ToolSpec(
        name="gateway_clmm_collect_fees",
        description="Collect fees from a CLMM position.",
        input_schema={
            "type": "object",
            "properties": {
                "connector": {"type": "string"},
                "network": {"type": "string"},
                "position_address": {"type": "string"},
                "wallet_address": {"type": "string"},
            },
            "required": ["connector", "network", "position_address"],
        },
        handler=_gateway_clmm_collect_fees,
    ),
    ToolSpec(
        name="gateway_clmm_positions_owned",
        description="List CLMM positions owned for a pool.",
        input_schema={
            "type": "object",
            "properties": {
                "connector": {"type": "string"},
                "network": {"type": "string"},
                "pool_address": {"type": "string"},
                "wallet_address": {"type": "string"},
            },
            "required": ["connector", "network", "pool_address"],
        },
        handler=_gateway_clmm_positions_owned,
    ),
    ToolSpec(
        name="gateway_clmm_position_events",
        description="Get CLMM position events.",
        input_schema={
            "type": "object",
            "properties": {
                "position_address": {"type": "string"},
                "event_type": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["position_address"],
        },
        handler=_gateway_clmm_position_events,
    ),
    ToolSpec(
        name="gateway_clmm_positions_search",
        description="Search CLMM positions with filters.",
        input_schema={
            "type": "object",
            "properties": {
                "network": {"type": "string"},
                "connector": {"type": "string"},
                "wallet_address": {"type": "string"},
                "trading_pair": {"type": "string"},
                "status": {"type": "string"},
                "position_addresses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "refresh": {"type": "boolean"},
            },
        },
        handler=_gateway_clmm_positions_search,
    ),
    ToolSpec(
        name="bot_status",
        description="Get status of all active bots.",
        input_schema={"type": "object", "properties": {}},
        handler=_bot_status,
    ),
    ToolSpec(
        name="bot_instances",
        description="Get unified instance status (Docker + MQTT).",
        input_schema={"type": "object", "properties": {}},
        handler=_bot_instances,
    ),
    ToolSpec(
        name="bot_start",
        description="Start a bot.",
        input_schema={
            "type": "object",
            "properties": {
                "bot_name": {"type": "string"},
                "log_level": {"type": "string"},
                "script": {"type": "string"},
                "conf": {"type": "string"},
                "async_backend": {"type": "boolean"},
            },
            "required": ["bot_name"],
        },
        handler=_bot_start,
    ),
    ToolSpec(
        name="bot_stop",
        description="Stop a bot.",
        input_schema={
            "type": "object",
            "properties": {
                "bot_name": {"type": "string"},
                "skip_order_cancellation": {"type": "boolean"},
                "async_backend": {"type": "boolean"},
            },
            "required": ["bot_name"],
        },
        handler=_bot_stop,
    ),
    ToolSpec(
        name="bot_deploy_v2_script",
        description="Deploy a V2 script instance.",
        input_schema={
            "type": "object",
            "properties": {
                "instance_name": {"type": "string"},
                "credentials_profile": {"type": "string"},
                "image": {"type": "string"},
                "script": {"type": "string"},
                "script_config": {"type": "string"},
                "gateway_network_id": {"type": "string"},
                "gateway_wallet_address": {"type": "string"},
                "apply_gateway_defaults": {"type": "boolean"},
                "headless": {"type": "boolean"},
            },
            "required": ["instance_name", "credentials_profile"],
        },
        handler=_bot_deploy_v2_script,
    ),
    ToolSpec(
        name="bot_deploy_v2_controllers",
        description="Deploy a V2 controllers instance.",
        input_schema={
            "type": "object",
            "properties": {
                "instance_name": {"type": "string"},
                "credentials_profile": {"type": "string"},
                "controllers_config": {"type": "array", "items": {"type": "string"}},
                "max_global_drawdown_quote": {"type": "number"},
                "max_controller_drawdown_quote": {"type": "number"},
                "gateway_network_id": {"type": "string"},
                "gateway_wallet_address": {"type": "string"},
                "apply_gateway_defaults": {"type": "boolean"},
                "unique_instance_name": {"type": "boolean"},
                "image": {"type": "string"},
                "headless": {"type": "boolean"},
            },
            "required": ["instance_name", "credentials_profile", "controllers_config"],
        },
        handler=_bot_deploy_v2_controllers,
    ),
    ToolSpec(
        name="bot_stop_and_archive",
        description="Stop and archive a bot (background).",
        input_schema={
            "type": "object",
            "properties": {
                "bot_name": {"type": "string"},
                "skip_order_cancellation": {"type": "boolean"},
                "archive_locally": {"type": "boolean"},
                "s3_bucket": {"type": "string"},
            },
            "required": ["bot_name"],
        },
        handler=_bot_stop_and_archive,
    ),
    ToolSpec(
        name="controller_configs_list",
        description="List controller configs for a bot.",
        input_schema={
            "type": "object",
            "properties": {"bot_name": {"type": "string"}},
            "required": ["bot_name"],
        },
        handler=_controller_configs_list,
    ),
    ToolSpec(
        name="controller_config_update",
        description="Update a controller config for a bot.",
        input_schema={
            "type": "object",
            "properties": {
                "bot_name": {"type": "string"},
                "controller_name": {"type": "string"},
                "config": {"type": "object", "additionalProperties": True},
            },
            "required": ["bot_name", "controller_name", "config"],
        },
        handler=_controller_config_update,
    ),
    ToolSpec(
        name="script_configs_list",
        description="List all script configs.",
        input_schema={"type": "object", "properties": {}},
        handler=_script_configs_list,
    ),
    ToolSpec(
        name="script_config_get",
        description="Get a script config by name.",
        input_schema={
            "type": "object",
            "properties": {"config_name": {"type": "string"}},
            "required": ["config_name"],
        },
        handler=_script_config_get,
    ),
    ToolSpec(
        name="script_config_upsert",
        description="Create or update a script config.",
        input_schema={
            "type": "object",
            "properties": {
                "config_name": {"type": "string"},
                "config": {"type": "object", "additionalProperties": True},
            },
            "required": ["config_name", "config"],
        },
        handler=_script_config_upsert,
    ),
    ToolSpec(
        name="script_config_delete",
        description="Delete a script config.",
        input_schema={
            "type": "object",
            "properties": {"config_name": {"type": "string"}},
            "required": ["config_name"],
        },
        handler=_script_config_delete,
    ),
    ToolSpec(
        name="script_config_template",
        description="Get a script config template by script name.",
        input_schema={
            "type": "object",
            "properties": {"script_name": {"type": "string"}},
            "required": ["script_name"],
        },
        handler=_script_config_template,
    ),
    ToolSpec(
        name="controller_configs_list_global",
        description="List all controller configs (global).",
        input_schema={"type": "object", "properties": {}},
        handler=_controller_configs_list_global,
    ),
    ToolSpec(
        name="controller_config_get",
        description="Get a controller config by name.",
        input_schema={
            "type": "object",
            "properties": {"config_name": {"type": "string"}},
            "required": ["config_name"],
        },
        handler=_controller_config_get,
    ),
    ToolSpec(
        name="controller_config_upsert",
        description="Create or update a controller config.",
        input_schema={
            "type": "object",
            "properties": {
                "config_name": {"type": "string"},
                "config": {"type": "object", "additionalProperties": True},
            },
            "required": ["config_name", "config"],
        },
        handler=_controller_config_upsert,
    ),
    ToolSpec(
        name="controller_config_delete",
        description="Delete a controller config.",
        input_schema={
            "type": "object",
            "properties": {"config_name": {"type": "string"}},
            "required": ["config_name"],
        },
        handler=_controller_config_delete,
    ),
    ToolSpec(
        name="controller_config_template",
        description="Get a controller config template.",
        input_schema={
            "type": "object",
            "properties": {
                "controller_type": {"type": "string"},
                "controller_name": {"type": "string"},
            },
            "required": ["controller_type", "controller_name"],
        },
        handler=_controller_config_template,
    ),
    ToolSpec(
        name="controller_config_validate",
        description="Validate a controller config payload.",
        input_schema={
            "type": "object",
            "properties": {
                "controller_type": {"type": "string"},
                "controller_name": {"type": "string"},
                "config": {"type": "object", "additionalProperties": True},
            },
            "required": ["controller_type", "controller_name", "config"],
        },
        handler=_controller_config_validate,
    ),
    ToolSpec(
        name="deploy_v2_workflow_plan",
        description="Read-only plan for deploy-v2 workflow (tokens, pools, approvals, config, deploy).",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "network": {"type": "string"},
                "connector_name": {"type": "string"},
                "pool_type": {"type": "string"},
                "pool_address": {"type": "string"},
                "base": {"type": "string"},
                "quote": {"type": "string"},
                "base_address": {"type": "string"},
                "quote_address": {"type": "string"},
                "fee_pct": {"type": "number"},
                "tokens": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "string"},
                            "symbol": {"type": "string"},
                            "name": {"type": "string"},
                            "decimals": {"type": "integer"},
                        },
                    },
                },
                "wallet_address": {"type": "string"},
                "spender": {"type": "string"},
                "approval_amount": {"type": "string"},
                "deployment_type": {"type": "string", "enum": ["script", "controllers"]},
                "instance_name": {"type": "string"},
                "credentials_profile": {"type": "string"},
                "image": {"type": "string"},
                "headless": {"type": "boolean"},
                "gateway_network_id": {"type": "string"},
                "gateway_wallet_address": {"type": "string"},
                "apply_gateway_defaults": {"type": "boolean"},
                "unique_instance_name": {"type": "boolean"},
                "script": {"type": "string"},
                "script_config": {"type": "string"},
                "controllers_config": {"type": "array", "items": {"type": "string"}},
                "gateway_passphrase": {"type": "string"},
                "gateway_image": {"type": "string"},
                "gateway_port": {"type": "integer"},
                "gateway_dev_mode": {"type": "boolean"},
            },
        },
        handler=_deploy_v2_workflow_plan,
    ),
    ToolSpec(
        name="metadata_token",
        description="Fetch token metadata via Gateway (GeckoTerminal-backed).",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["network_id", "address"],
        },
        handler=_metadata_token,
    ),
    ToolSpec(
        name="metadata_pools",
        description="Search pools via Gateway metadata (GeckoTerminal-backed).",
        input_schema={
            "type": "object",
            "properties": {
                "network_id": {"type": "string"},
                "connector": {"type": "string"},
                "pool_type": {"type": "string"},
                "token_a": {"type": "string"},
                "token_b": {"type": "string"},
                "search": {"type": "string"},
                "pages": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["network_id"],
        },
        handler=_metadata_pools,
    ),
]

_TOOL_INDEX = {spec.name: spec for spec in _TOOL_SPECS}


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            # For no-arg tools, prefer an explicit empty-object schema.
            # This matches MCP examples and helps clients validate tool calls.
            "inputSchema": (
                {**spec.input_schema, "additionalProperties": False}
                if spec.input_schema.get("type") == "object"
                and spec.input_schema.get("properties") == {}
                and "additionalProperties" not in spec.input_schema
                and "required" not in spec.input_schema
                else spec.input_schema
            ),
        }
        for spec in _TOOL_SPECS
    ]


def dispatch_tool(name: str, arguments: dict, http_client: McpHttpClient) -> Any:
    spec = _TOOL_INDEX.get(name)
    if spec is None:
        raise UnknownToolError(f"Unknown tool: {name}")
    return spec.handler(arguments or {}, http_client)
