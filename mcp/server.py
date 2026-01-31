"""MCP stdio server for Hummingbot API."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from mcp.handlers import dispatch_tool
from mcp.http_client import McpHttpClient, McpHttpError
from mcp.tools import tool_definitions


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
            return self._ok(msg_id, {"tools": tool_definitions()})
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
            result = dispatch_tool(name, arguments, self.http_client)
            return self._tool_result(msg_id, result)
        except McpHttpError as exc:
            return self._tool_error(msg_id, f"HTTP {exc.status_code}: {exc.message}")
        except ValueError as exc:
            return self._error(msg_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(msg_id, -32603, f"Server error: {exc}")

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
    _load_dotenv()

    base_url = os.getenv("MCP_HUMMINGBOT_API_URL", "http://127.0.0.1:8000")
    username = os.getenv("MCP_HUMMINGBOT_API_USERNAME", "")
    password = os.getenv("MCP_HUMMINGBOT_API_PASSWORD", "")
    timeout_seconds = float(os.getenv("MCP_HUMMINGBOT_API_TIMEOUT_SECONDS", "10"))

    try:
        http_client = McpHttpClient(base_url, username, password, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)

    server = McpServer(http_client)
    run_stdio(server)




def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader to avoid external dependencies."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        return


if __name__ == "__main__":
    main()
