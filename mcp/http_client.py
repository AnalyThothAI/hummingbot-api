"""HTTP client for MCP adapter."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class McpHttpError(Exception):
    """HTTP request error."""

    status_code: int
    message: str


class McpHttpClient:
    """HTTP client for local Hummingbot API."""

    def __init__(self, base_url: str, username: str, password: str, timeout_seconds: float = 10.0) -> None:
        if not username or not password:
            raise ValueError("MCP_HUMMINGBOT_API_USERNAME and MCP_HUMMINGBOT_API_PASSWORD are required")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        auth_raw = f"{username}:{password}".encode("utf-8")
        self._auth_header = "Basic " + base64.b64encode(auth_raw).decode("ascii")

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        """Send GET request."""
        return self._request("GET", path, params=params)

    def post(self, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None) -> Any:
        """Send POST request."""
        return self._request("POST", path, params=params, json_body=json_body)

    def delete(self, path: str, params: Optional[dict] = None) -> Any:
        """Send DELETE request."""
        return self._request("DELETE", path, params=params)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
        }
        data = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode("utf-8")

        req = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read()
        except HTTPError as exc:
            try:
                body = exc.read()
            except Exception:
                body = b""
            raise McpHttpError(exc.code, body.decode("utf-8", errors="replace")) from exc
        except Exception as exc:
            raise McpHttpError(0, str(exc)) from exc

        if status >= 400:
            raise McpHttpError(status, body.decode("utf-8", errors="replace"))

        if not body:
            return None

        # Most endpoints return JSON. Keep a safe fallback for non-JSON responses.
        text = body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
