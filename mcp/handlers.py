"""Tool handlers for MCP adapter."""

from __future__ import annotations

from typing import Any, Iterable

from mcp.http_client import McpHttpClient
from mcp.workflows import build_deploy_v2_workflow_plan


def dispatch_tool(name: str, arguments: dict, http_client: McpHttpClient) -> Any:
    if name == "gateway_status":
        return http_client.get("/gateway/status")
    if name == "gateway_start":
        passphrase = arguments.get("passphrase")
        if not passphrase:
            raise ValueError("passphrase is required")
        return http_client.post("/gateway/start", json_body=arguments)
    if name == "gateway_stop":
        return http_client.post("/gateway/stop")
    if name == "gateway_restart":
        payload = arguments or None
        return http_client.post("/gateway/restart", json_body=payload)
    if name == "gateway_logs":
        params = _pick_params(arguments, ["tail"])
        return http_client.get("/gateway/logs", params=params)
    if name == "gateway_connectors":
        return http_client.get("/gateway/connectors")
    if name == "gateway_connector_config":
        connector_name = arguments.get("connector_name")
        if not connector_name:
            raise ValueError("connector_name is required")
        return http_client.get(f"/gateway/connectors/{connector_name}")
    if name == "gateway_tokens_list":
        network_id = arguments.get("network_id")
        if not network_id:
            raise ValueError("network_id is required")
        params = _pick_params(arguments, ["search"])
        return http_client.get(f"/gateway/networks/{network_id}/tokens", params=params)
    if name == "gateway_token_add":
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
    if name == "gateway_token_delete":
        network_id = arguments.get("network_id")
        token_address = arguments.get("token_address")
        if not network_id:
            raise ValueError("network_id is required")
        if not token_address:
            raise ValueError("token_address is required")
        return http_client.delete(f"/gateway/networks/{network_id}/tokens/{token_address}")
    if name == "gateway_pools_list":
        connector_name = arguments.get("connector_name")
        if not connector_name:
            raise ValueError("connector_name is required")
        params = _pick_params(arguments, ["connector_name", "network", "pool_type", "search"])
        return http_client.get("/gateway/pools", params=params)
    if name == "gateway_pool_add":
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
    if name == "gateway_pool_delete":
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
    if name == "gateway_allowances":
        payload = _pick_params(arguments, ["network_id", "chain", "network", "address", "tokens", "spender"])
        if "address" not in payload:
            raise ValueError("address is required")
        if "tokens" not in payload:
            raise ValueError("tokens is required")
        if "spender" not in payload:
            raise ValueError("spender is required")
        return http_client.post("/gateway/allowances", json_body=payload)
    if name == "gateway_approve":
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
    if name == "gateway_swap_quote":
        payload = _pick_params(arguments, ["connector", "network", "trading_pair", "side", "amount", "slippage_pct"])
        for key in ("connector", "network", "trading_pair", "side", "amount"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/gateway/swap/quote", json_body=payload)
    if name == "gateway_swap_execute":
        payload = _pick_params(
            arguments,
            ["connector", "network", "trading_pair", "side", "amount", "slippage_pct", "wallet_address"],
        )
        for key in ("connector", "network", "trading_pair", "side", "amount"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/gateway/swap/execute", json_body=payload)
    if name == "gateway_clmm_pool_info":
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
    if name == "gateway_clmm_pools":
        connector = arguments.get("connector")
        if not connector:
            raise ValueError("connector is required")
        params = _pick_params(arguments, ["connector", "page", "limit", "search_term", "sort_key", "order_by", "include_unknown"])
        return http_client.get("/gateway/clmm/pools", params=params)
    if name == "gateway_clmm_open":
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
    if name == "gateway_clmm_close":
        payload = _pick_params(arguments, ["connector", "network", "position_address", "wallet_address"])
        for key in ("connector", "network", "position_address"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/gateway/clmm/close", json_body=payload)
    if name == "gateway_clmm_collect_fees":
        payload = _pick_params(arguments, ["connector", "network", "position_address", "wallet_address"])
        for key in ("connector", "network", "position_address"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/gateway/clmm/collect-fees", json_body=payload)
    if name == "gateway_clmm_positions_owned":
        payload = _pick_params(arguments, ["connector", "network", "pool_address", "wallet_address"])
        for key in ("connector", "network", "pool_address"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/gateway/clmm/positions_owned", json_body=payload)
    if name == "gateway_clmm_position_events":
        position_address = arguments.get("position_address")
        if not position_address:
            raise ValueError("position_address is required")
        params = _pick_params(arguments, ["event_type", "limit"])
        return http_client.get(f"/gateway/clmm/positions/{position_address}/events", params=params)
    if name == "gateway_clmm_positions_search":
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
    if name == "bot_status":
        return http_client.get("/bot-orchestration/status")
    if name == "bot_instances":
        return http_client.get("/bot-orchestration/instances")
    if name == "bot_start":
        bot_name = arguments.get("bot_name")
        if not bot_name:
            raise ValueError("bot_name is required")
        payload = _pick_params(arguments, ["bot_name", "log_level", "script", "conf", "async_backend"])
        return http_client.post("/bot-orchestration/start-bot", json_body=payload)
    if name == "bot_stop":
        bot_name = arguments.get("bot_name")
        if not bot_name:
            raise ValueError("bot_name is required")
        payload = _pick_params(arguments, ["bot_name", "skip_order_cancellation", "async_backend"])
        return http_client.post("/bot-orchestration/stop-bot", json_body=payload)
    if name == "bot_deploy_v2_script":
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
                "headless",
            ],
        )
        for key in ("instance_name", "credentials_profile"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/bot-orchestration/deploy-v2-script", json_body=payload)
    if name == "bot_deploy_v2_controllers":
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
                "image",
                "headless",
            ],
        )
        for key in ("instance_name", "credentials_profile", "controllers_config"):
            if key not in payload:
                raise ValueError(f"{key} is required")
        return http_client.post("/bot-orchestration/deploy-v2-controllers", json_body=payload)
    if name == "bot_stop_and_archive":
        bot_name = arguments.get("bot_name")
        if not bot_name:
            raise ValueError("bot_name is required")
        params = _pick_params(arguments, ["skip_order_cancellation", "archive_locally", "s3_bucket"])
        return http_client.post(f"/bot-orchestration/stop-and-archive-bot/{bot_name}", params=params)
    if name == "controller_configs_list":
        bot_name = arguments.get("bot_name")
        if not bot_name:
            raise ValueError("bot_name is required")
        return http_client.get(f"/controllers/bots/{bot_name}/configs")
    if name == "controller_config_update":
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
    if name == "script_configs_list":
        return http_client.get("/scripts/configs")
    if name == "script_config_get":
        config_name = arguments.get("config_name")
        if not config_name:
            raise ValueError("config_name is required")
        return http_client.get(f"/scripts/configs/{config_name}")
    if name == "script_config_upsert":
        config_name = arguments.get("config_name")
        config = arguments.get("config")
        if not config_name:
            raise ValueError("config_name is required")
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        return http_client.post(f"/scripts/configs/{config_name}", json_body=config)
    if name == "script_config_delete":
        config_name = arguments.get("config_name")
        if not config_name:
            raise ValueError("config_name is required")
        return http_client.delete(f"/scripts/configs/{config_name}")
    if name == "script_config_template":
        script_name = arguments.get("script_name")
        if not script_name:
            raise ValueError("script_name is required")
        return http_client.get(f"/scripts/{script_name}/config/template")
    if name == "controller_configs_list_global":
        return http_client.get("/controllers/configs")
    if name == "controller_config_get":
        config_name = arguments.get("config_name")
        if not config_name:
            raise ValueError("config_name is required")
        return http_client.get(f"/controllers/configs/{config_name}")
    if name == "controller_config_upsert":
        config_name = arguments.get("config_name")
        config = arguments.get("config")
        if not config_name:
            raise ValueError("config_name is required")
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        return http_client.post(f"/controllers/configs/{config_name}", json_body=config)
    if name == "controller_config_delete":
        config_name = arguments.get("config_name")
        if not config_name:
            raise ValueError("config_name is required")
        return http_client.delete(f"/controllers/configs/{config_name}")
    if name == "controller_config_template":
        controller_type = arguments.get("controller_type")
        controller_name = arguments.get("controller_name")
        if not controller_type:
            raise ValueError("controller_type is required")
        if not controller_name:
            raise ValueError("controller_name is required")
        return http_client.get(f"/controllers/{controller_type}/{controller_name}/config/template")
    if name == "controller_config_validate":
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
    if name == "deploy_v2_workflow_plan":
        return build_deploy_v2_workflow_plan(arguments, http_client)

    raise ValueError(f"Unknown tool: {name}")


def _pick_params(arguments: dict, keys: Iterable[str]) -> dict:
    payload = {}
    for key in keys:
        if key in arguments and arguments[key] is not None:
            payload[key] = arguments[key]
    return payload
