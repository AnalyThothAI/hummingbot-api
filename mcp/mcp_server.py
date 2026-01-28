"""MCP stdio server (thin adapter) for Hummingbot API."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import httpx


@dataclass
class McpHttpError(Exception):
    """HTTP request error."""

    status_code: int
    message: str


class McpHttpClient:
    """HTTP client for local Hummingbot API."""

    def __init__(self, base_url: str, username: str, password: str, timeout_seconds: float = 10.0) -> None:
        if not username or not password:
            raise ValueError("HUMMINGBOT_API_USERNAME and HUMMINGBOT_API_PASSWORD are required")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout_seconds,
            trust_env=False,
            auth=(username, password),
        )

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        """Send GET request."""
        response = self._client.get(f"{self.base_url}{path}", params=params)
        return self._handle_response(response)

    def post(self, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None) -> Any:
        """Send POST request."""
        response = self._client.post(f"{self.base_url}{path}", params=params, json=json_body)
        return self._handle_response(response)

    def delete(self, path: str, params: Optional[dict] = None) -> Any:
        """Send DELETE request."""
        response = self._client.delete(f"{self.base_url}{path}", params=params)
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise McpHttpError(response.status_code, response.text)
        if response.content:
            return response.json()
        return None


class McpServer:
    """MCP stdio server (tool calls to local HTTP API)."""

    def __init__(self, http_client: McpHttpClient) -> None:
        self.http_client = http_client
        self.supported_versions = [
            "2025-11-25",
            "2025-06-18",
            "2025-03-26",
            "2024-11-05",
        ]
        self.initialized = False

    def handle_message(self, message: dict) -> Optional[dict]:
        """Handle a single JSON-RPC message."""
        method = message.get("method")
        msg_id = message.get("id")

        if method == "initialize":
            return self._handle_initialize(message, msg_id)
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method == "ping":
            return self._ok(msg_id, {})
        if method == "tools/list":
            return self._ok(msg_id, {"tools": self._tool_definitions()})
        if method == "tools/call":
            return self._handle_tool_call(message, msg_id)

        if msg_id is None:
            return None
        return self._error(msg_id, -32601, f"Unknown method: {method}")

    def _handle_initialize(self, message: dict, msg_id: Any) -> dict:
        params = message.get("params") or {}
        requested_version = params.get("protocolVersion")
        protocol_version = (
            requested_version
            if requested_version in self.supported_versions
            else self.supported_versions[0]
        )
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": "hummingbot-api-mcp",
                "version": "0.1.0",
            },
            "instructions": "Start hummingbot-api locally before using tools.",
        }
        return self._ok(msg_id, result)

    def _handle_tool_call(self, message: dict, msg_id: Any) -> dict:
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if not name:
            return self._error(msg_id, -32602, "Missing tool name")

        try:
            result = self._dispatch_tool(name, arguments)
            return self._tool_result(msg_id, result)
        except McpHttpError as exc:
            return self._tool_error(msg_id, f"HTTP {exc.status_code}: {exc.message}")
        except ValueError as exc:
            return self._error(msg_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(msg_id, -32603, f"Server error: {exc}")

    def _dispatch_tool(self, name: str, arguments: dict) -> Any:
        if name == "gateway_status":
            return self.http_client.get("/gateway/status")
        if name == "gateway_start":
            passphrase = arguments.get("passphrase")
            if not passphrase:
                raise ValueError("passphrase is required")
            return self.http_client.post("/gateway/start", json_body=arguments)
        if name == "gateway_stop":
            return self.http_client.post("/gateway/stop")
        if name == "gateway_restart":
            payload = arguments or None
            return self.http_client.post("/gateway/restart", json_body=payload)
        if name == "gateway_logs":
            params = self._pick_params(arguments, ["tail"])
            return self.http_client.get("/gateway/logs", params=params)
        if name == "gateway_connectors":
            return self.http_client.get("/gateway/connectors")
        if name == "gateway_connector_config":
            connector_name = arguments.get("connector_name")
            if not connector_name:
                raise ValueError("connector_name is required")
            return self.http_client.get(f"/gateway/connectors/{connector_name}")
        if name == "gateway_tokens_list":
            network_id = arguments.get("network_id")
            if not network_id:
                raise ValueError("network_id is required")
            params = self._pick_params(arguments, ["search"])
            return self.http_client.get(f"/gateway/networks/{network_id}/tokens", params=params)
        if name == "gateway_token_add":
            network_id = arguments.get("network_id")
            if not network_id:
                raise ValueError("network_id is required")
            payload = self._pick_params(arguments, ["address", "symbol", "name", "decimals"])
            if "address" not in payload:
                raise ValueError("address is required")
            if "symbol" not in payload:
                raise ValueError("symbol is required")
            if "decimals" not in payload:
                raise ValueError("decimals is required")
            return self.http_client.post(f"/gateway/networks/{network_id}/tokens", json_body=payload)
        if name == "gateway_token_delete":
            network_id = arguments.get("network_id")
            token_address = arguments.get("token_address")
            if not network_id:
                raise ValueError("network_id is required")
            if not token_address:
                raise ValueError("token_address is required")
            return self.http_client.delete(f"/gateway/networks/{network_id}/tokens/{token_address}")
        if name == "gateway_pools_list":
            connector_name = arguments.get("connector_name")
            if not connector_name:
                raise ValueError("connector_name is required")
            params = self._pick_params(arguments, ["connector_name", "network", "pool_type", "search"])
            return self.http_client.get("/gateway/pools", params=params)
        if name == "gateway_pool_add":
            payload = self._pick_params(
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
            return self.http_client.post("/gateway/pools", json_body=payload)
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
            return self.http_client.delete(f"/gateway/pools/{address}", params=params)
        if name == "gateway_allowances":
            payload = self._pick_params(arguments, ["network_id", "chain", "network", "address", "tokens", "spender"])
            if "address" not in payload:
                raise ValueError("address is required")
            if "tokens" not in payload:
                raise ValueError("tokens is required")
            if "spender" not in payload:
                raise ValueError("spender is required")
            return self.http_client.post("/gateway/allowances", json_body=payload)
        if name == "gateway_approve":
            payload = self._pick_params(
                arguments,
                ["network_id", "chain", "network", "address", "token", "spender", "amount"],
            )
            if "address" not in payload:
                raise ValueError("address is required")
            if "token" not in payload:
                raise ValueError("token is required")
            if "spender" not in payload:
                raise ValueError("spender is required")
            return self.http_client.post("/gateway/approve", json_body=payload)
        if name == "bot_status":
            return self.http_client.get("/bot-orchestration/status")
        if name == "bot_instances":
            return self.http_client.get("/bot-orchestration/instances")
        if name == "bot_start":
            bot_name = arguments.get("bot_name")
            if not bot_name:
                raise ValueError("bot_name is required")
            payload = self._pick_params(arguments, ["bot_name", "log_level", "script", "conf", "async_backend"])
            return self.http_client.post("/bot-orchestration/start-bot", json_body=payload)
        if name == "bot_stop":
            bot_name = arguments.get("bot_name")
            if not bot_name:
                raise ValueError("bot_name is required")
            payload = self._pick_params(arguments, ["bot_name", "skip_order_cancellation", "async_backend"])
            return self.http_client.post("/bot-orchestration/stop-bot", json_body=payload)
        if name == "bot_deploy_v2_script":
            payload = self._pick_params(
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
            return self.http_client.post("/bot-orchestration/deploy-v2-script", json_body=payload)
        if name == "bot_deploy_v2_controllers":
            payload = self._pick_params(
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
            return self.http_client.post("/bot-orchestration/deploy-v2-controllers", json_body=payload)
        if name == "bot_stop_and_archive":
            bot_name = arguments.get("bot_name")
            if not bot_name:
                raise ValueError("bot_name is required")
            params = self._pick_params(arguments, ["skip_order_cancellation", "archive_locally", "s3_bucket"])
            return self.http_client.post(f"/bot-orchestration/stop-and-archive-bot/{bot_name}", params=params)
        if name == "controller_configs_list":
            bot_name = arguments.get("bot_name")
            if not bot_name:
                raise ValueError("bot_name is required")
            return self.http_client.get(f"/controllers/bots/{bot_name}/configs")
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
            return self.http_client.post(
                f"/controllers/bots/{bot_name}/{controller_name}/config",
                json_body=config,
            )

        raise ValueError(f"Unknown tool: {name}")

    @staticmethod
    def _pick_params(arguments: dict, keys: Iterable[str]) -> dict:
        payload = {}
        for key in keys:
            if key in arguments and arguments[key] is not None:
                payload[key] = arguments[key]
        return payload

    @staticmethod
    def _tool_result(msg_id: Any, data: Any) -> dict:
        text = json.dumps(data, ensure_ascii=False)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            },
        }

    @staticmethod
    def _tool_error(msg_id: Any, text: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": True,
            },
        }

    @staticmethod
    def _ok(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_definitions() -> List[Dict[str, Any]]:
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
        ]


def run_stdio(server: McpServer) -> None:
    """Run MCP stdio event loop."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            _write_response(response)
            continue

        responses = _handle_payload(server, payload)
        if responses is None:
            continue
        _write_response(responses)


def _handle_payload(server: McpServer, payload: Any) -> Optional[Any]:
    if isinstance(payload, list):
        results = []
        for item in payload:
            if not isinstance(item, dict):
                results.append(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
                )
                continue
            response = server.handle_message(item)
            if response is not None:
                results.append(response)
        return results or None

    if not isinstance(payload, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}

    return server.handle_message(payload)


def _write_response(response: Any) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    """MCP stdio entrypoint."""
    base_url = os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8000")
    username = os.getenv("HUMMINGBOT_API_USERNAME", "")
    password = os.getenv("HUMMINGBOT_API_PASSWORD", "")
    timeout_seconds = float(os.getenv("HUMMINGBOT_API_TIMEOUT_SECONDS", "10"))

    try:
        http_client = McpHttpClient(base_url, username, password, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)

    server = McpServer(http_client)
    run_stdio(server)


if __name__ == "__main__":
    main()
