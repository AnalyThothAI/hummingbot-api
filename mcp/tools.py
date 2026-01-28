"""Tool definitions for MCP adapter."""

from __future__ import annotations

from typing import Any, Dict, List


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "gateway_status",
            "description": "Get Gateway container status.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "gateway_start",
            "description": "Start Gateway container.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "passphrase": {"type": "string"},
                    "image": {"type": "string"},
                    "port": {"type": "integer"},
                    "dev_mode": {"type": "boolean"},
                },
                "required": ["passphrase"],
            },
        },
        {
            "name": "gateway_stop",
            "description": "Stop Gateway container.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "gateway_restart",
            "description": "Restart Gateway container (optional config).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "passphrase": {"type": "string"},
                    "image": {"type": "string"},
                    "port": {"type": "integer"},
                    "dev_mode": {"type": "boolean"},
                },
            },
        },
        {
            "name": "gateway_logs",
            "description": "Get Gateway container logs.",
            "inputSchema": {
                "type": "object",
                "properties": {"tail": {"type": "integer"}},
            },
        },
        {
            "name": "gateway_connectors",
            "description": "List available DEX connectors from Gateway.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "gateway_connector_config",
            "description": "Get Gateway connector configuration.",
            "inputSchema": {
                "type": "object",
                "properties": {"connector_name": {"type": "string"}},
                "required": ["connector_name"],
            },
        },
        {
            "name": "gateway_tokens_list",
            "description": "List tokens for a Gateway network.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "network_id": {"type": "string"},
                    "search": {"type": "string"},
                },
                "required": ["network_id"],
            },
        },
        {
            "name": "gateway_token_add",
            "description": "Add a custom token for a Gateway network.",
            "inputSchema": {
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
        },
        {
            "name": "gateway_token_delete",
            "description": "Delete a custom token from a Gateway network.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "network_id": {"type": "string"},
                    "token_address": {"type": "string"},
                },
                "required": ["network_id", "token_address"],
            },
        },
        {
            "name": "gateway_pools_list",
            "description": "List pools from Gateway.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector_name": {"type": "string"},
                    "network": {"type": "string"},
                    "pool_type": {"type": "string"},
                    "search": {"type": "string"},
                },
                "required": ["connector_name"],
            },
        },
        {
            "name": "gateway_pool_add",
            "description": "Add a custom pool to Gateway.",
            "inputSchema": {
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
        },
        {
            "name": "gateway_pool_delete",
            "description": "Delete a pool from Gateway.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "connector_name": {"type": "string"},
                    "network": {"type": "string"},
                    "pool_type": {"type": "string"},
                },
                "required": ["address", "connector_name", "network", "pool_type"],
            },
        },
        {
            "name": "gateway_allowances",
            "description": "Get ERC20 token allowances via Gateway.",
            "inputSchema": {
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
        },
        {
            "name": "gateway_approve",
            "description": "Approve ERC20 token spending via Gateway.",
            "inputSchema": {
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
        },
        {
            "name": "gateway_swap_quote",
            "description": "Get a swap quote via Gateway router.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector": {"type": "string"},
                    "network": {"type": "string"},
                    "trading_pair": {"type": "string"},
                    "side": {"type": "string"},
                    "amount": {"type": "number"},
                    "slippage_pct": {"type": "number"},
                },
                "required": ["connector", "network", "trading_pair", "side", "amount"],
            },
        },
        {
            "name": "gateway_swap_execute",
            "description": "Execute a swap via Gateway router.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector": {"type": "string"},
                    "network": {"type": "string"},
                    "trading_pair": {"type": "string"},
                    "side": {"type": "string"},
                    "amount": {"type": "number"},
                    "slippage_pct": {"type": "number"},
                    "wallet_address": {"type": "string"},
                },
                "required": ["connector", "network", "trading_pair", "side", "amount"],
            },
        },
        {
            "name": "gateway_clmm_pool_info",
            "description": "Get CLMM pool info.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector": {"type": "string"},
                    "network": {"type": "string"},
                    "pool_address": {"type": "string"},
                },
                "required": ["connector", "network", "pool_address"],
            },
        },
        {
            "name": "gateway_clmm_pools",
            "description": "List CLMM pools for a connector.",
            "inputSchema": {
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
        },
        {
            "name": "gateway_clmm_open",
            "description": "Open a CLMM position.",
            "inputSchema": {
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
        },
        {
            "name": "gateway_clmm_close",
            "description": "Close a CLMM position.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector": {"type": "string"},
                    "network": {"type": "string"},
                    "position_address": {"type": "string"},
                    "wallet_address": {"type": "string"},
                },
                "required": ["connector", "network", "position_address"],
            },
        },
        {
            "name": "gateway_clmm_collect_fees",
            "description": "Collect fees from a CLMM position.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector": {"type": "string"},
                    "network": {"type": "string"},
                    "position_address": {"type": "string"},
                    "wallet_address": {"type": "string"},
                },
                "required": ["connector", "network", "position_address"],
            },
        },
        {
            "name": "gateway_clmm_positions_owned",
            "description": "List CLMM positions owned for a pool.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector": {"type": "string"},
                    "network": {"type": "string"},
                    "pool_address": {"type": "string"},
                    "wallet_address": {"type": "string"},
                },
                "required": ["connector", "network", "pool_address"],
            },
        },
        {
            "name": "gateway_clmm_position_events",
            "description": "Get CLMM position events.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "position_address": {"type": "string"},
                    "event_type": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["position_address"],
            },
        },
        {
            "name": "gateway_clmm_positions_search",
            "description": "Search CLMM positions with filters.",
            "inputSchema": {
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
        },
        {
            "name": "bot_status",
            "description": "Get status of all active bots.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bot_instances",
            "description": "Get unified instance status (Docker + MQTT).",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bot_start",
            "description": "Start a bot.",
            "inputSchema": {
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
        },
        {
            "name": "bot_stop",
            "description": "Stop a bot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bot_name": {"type": "string"},
                    "skip_order_cancellation": {"type": "boolean"},
                    "async_backend": {"type": "boolean"},
                },
                "required": ["bot_name"],
            },
        },
        {
            "name": "bot_deploy_v2_script",
            "description": "Deploy a V2 script instance.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "instance_name": {"type": "string"},
                    "credentials_profile": {"type": "string"},
                    "image": {"type": "string"},
                    "script": {"type": "string"},
                    "script_config": {"type": "string"},
                    "gateway_network_id": {"type": "string"},
                    "gateway_wallet_address": {"type": "string"},
                    "headless": {"type": "boolean"},
                },
                "required": ["instance_name", "credentials_profile"],
            },
        },
        {
            "name": "bot_deploy_v2_controllers",
            "description": "Deploy a V2 controllers instance.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "instance_name": {"type": "string"},
                    "credentials_profile": {"type": "string"},
                    "controllers_config": {"type": "array", "items": {"type": "string"}},
                    "max_global_drawdown_quote": {"type": "number"},
                    "max_controller_drawdown_quote": {"type": "number"},
                    "gateway_network_id": {"type": "string"},
                    "gateway_wallet_address": {"type": "string"},
                    "image": {"type": "string"},
                    "headless": {"type": "boolean"},
                },
                "required": ["instance_name", "credentials_profile", "controllers_config"],
            },
        },
        {
            "name": "bot_stop_and_archive",
            "description": "Stop and archive a bot (background).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bot_name": {"type": "string"},
                    "skip_order_cancellation": {"type": "boolean"},
                    "archive_locally": {"type": "boolean"},
                    "s3_bucket": {"type": "string"},
                },
                "required": ["bot_name"],
            },
        },
        {
            "name": "controller_configs_list",
            "description": "List controller configs for a bot.",
            "inputSchema": {
                "type": "object",
                "properties": {"bot_name": {"type": "string"}},
                "required": ["bot_name"],
            },
        },
        {
            "name": "controller_config_update",
            "description": "Update a controller config for a bot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bot_name": {"type": "string"},
                    "controller_name": {"type": "string"},
                    "config": {"type": "object", "additionalProperties": True},
                },
                "required": ["bot_name", "controller_name", "config"],
            },
        },
        {
            "name": "script_configs_list",
            "description": "List all script configs.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "script_config_get",
            "description": "Get a script config by name.",
            "inputSchema": {
                "type": "object",
                "properties": {"config_name": {"type": "string"}},
                "required": ["config_name"],
            },
        },
        {
            "name": "script_config_upsert",
            "description": "Create or update a script config.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "config_name": {"type": "string"},
                    "config": {"type": "object", "additionalProperties": True},
                },
                "required": ["config_name", "config"],
            },
        },
        {
            "name": "script_config_delete",
            "description": "Delete a script config.",
            "inputSchema": {
                "type": "object",
                "properties": {"config_name": {"type": "string"}},
                "required": ["config_name"],
            },
        },
        {
            "name": "script_config_template",
            "description": "Get a script config template by script name.",
            "inputSchema": {
                "type": "object",
                "properties": {"script_name": {"type": "string"}},
                "required": ["script_name"],
            },
        },
        {
            "name": "controller_configs_list_global",
            "description": "List all controller configs (global).",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "controller_config_get",
            "description": "Get a controller config by name.",
            "inputSchema": {
                "type": "object",
                "properties": {"config_name": {"type": "string"}},
                "required": ["config_name"],
            },
        },
        {
            "name": "controller_config_upsert",
            "description": "Create or update a controller config.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "config_name": {"type": "string"},
                    "config": {"type": "object", "additionalProperties": True},
                },
                "required": ["config_name", "config"],
            },
        },
        {
            "name": "controller_config_delete",
            "description": "Delete a controller config.",
            "inputSchema": {
                "type": "object",
                "properties": {"config_name": {"type": "string"}},
                "required": ["config_name"],
            },
        },
        {
            "name": "controller_config_template",
            "description": "Get a controller config template.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "controller_type": {"type": "string"},
                    "controller_name": {"type": "string"},
                },
                "required": ["controller_type", "controller_name"],
            },
        },
        {
            "name": "controller_config_validate",
            "description": "Validate a controller config payload.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "controller_type": {"type": "string"},
                    "controller_name": {"type": "string"},
                    "config": {"type": "object", "additionalProperties": True},
                },
                "required": ["controller_type", "controller_name", "config"],
            },
        },
        {
            "name": "deploy_v2_workflow_plan",
            "description": "Read-only plan for deploy-v2 workflow (tokens, pools, approvals, config, deploy).",
            "inputSchema": {
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
                    "script": {"type": "string"},
                    "script_config": {"type": "string"},
                    "controllers_config": {"type": "array", "items": {"type": "string"}},
                    "gateway_passphrase": {"type": "string"},
                    "gateway_image": {"type": "string"},
                    "gateway_port": {"type": "integer"},
                    "gateway_dev_mode": {"type": "boolean"},
                },
            },
        },
        {
            "name": "metadata_token",
            "description": "Fetch token metadata via Gateway (GeckoTerminal-backed).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "network_id": {"type": "string"},
                    "address": {"type": "string"},
                },
                "required": ["network_id", "address"],
            },
        },
        {
            "name": "metadata_pools",
            "description": "Search pools via Gateway metadata (GeckoTerminal-backed).",
            "inputSchema": {
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
        },
    ]
