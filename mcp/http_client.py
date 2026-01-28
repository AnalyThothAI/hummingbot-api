"""HTTP client for MCP adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

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
