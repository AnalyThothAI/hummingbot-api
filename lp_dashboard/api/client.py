"""API client for communicating with hummingbot-api."""
import httpx
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class APIConfig:
    """API connection configuration."""
    base_url: str
    username: str
    password: str
    timeout: float = 30.0


class LPDashboardAPI:
    """Client for hummingbot-api endpoints."""

    def __init__(self, config: APIConfig):
        self.config = config
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy initialization of HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                auth=(self.config.username, self.config.password),
                timeout=self.config.timeout,
            )
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make an HTTP request to the API."""
        try:
            response = self.client.request(
                method=method,
                url=endpoint,
                params=params,
                json=json,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise

    # ==================== Bot Orchestration ====================

    def get_all_bots_status(self) -> Dict[str, Any]:
        """Get status of all active bots."""
        return self._request("GET", "/bot-orchestration/status")

    def get_bot_status(self, bot_name: str) -> Dict[str, Any]:
        """Get status of a specific bot."""
        return self._request("GET", f"/bot-orchestration/{bot_name}/status")

    def get_mqtt_status(self) -> Dict[str, Any]:
        """Get MQTT connection status and discovered bots."""
        return self._request("GET", "/bot-orchestration/mqtt")

    def start_bot(
        self,
        bot_name: str,
        script: Optional[str] = None,
        conf: Optional[str] = None,
        log_level: str = "INFO",
        async_backend: bool = True,
    ) -> Dict[str, Any]:
        """Start a bot with the specified configuration."""
        payload = {
            "bot_name": bot_name,
            "log_level": log_level,
            "async_backend": async_backend,
        }
        if script:
            payload["script"] = script
        if conf:
            payload["conf"] = conf
        return self._request("POST", "/bot-orchestration/start-bot", json=payload)

    def stop_bot(
        self,
        bot_name: str,
        skip_order_cancellation: bool = False,
        async_backend: bool = True,
    ) -> Dict[str, Any]:
        """Stop a running bot."""
        payload = {
            "bot_name": bot_name,
            "skip_order_cancellation": skip_order_cancellation,
            "async_backend": async_backend,
        }
        return self._request("POST", "/bot-orchestration/stop-bot", json=payload)

    def stop_and_archive_bot(
        self,
        bot_name: str,
        skip_order_cancellation: bool = True,
        archive_locally: bool = True,
        s3_bucket: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stop a bot and archive its data."""
        params = {
            "skip_order_cancellation": skip_order_cancellation,
            "archive_locally": archive_locally,
        }
        if s3_bucket:
            params["s3_bucket"] = s3_bucket
        return self._request(
            "POST",
            f"/bot-orchestration/stop-and-archive-bot/{bot_name}",
            params=params,
        )

    def get_bot_history(
        self,
        bot_name: str,
        days: int = 0,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Get trading history for a bot."""
        params = {"days": days, "verbose": verbose}
        return self._request(
            "GET",
            f"/bot-orchestration/{bot_name}/history",
            params=params,
        )

    def get_bot_runs(
        self,
        bot_name: Optional[str] = None,
        strategy_type: Optional[str] = None,
        run_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get bot runs with optional filtering."""
        params = {"limit": limit, "offset": offset}
        if bot_name:
            params["bot_name"] = bot_name
        if strategy_type:
            params["strategy_type"] = strategy_type
        if run_status:
            params["run_status"] = run_status
        return self._request("GET", "/bot-orchestration/bot-runs", params=params)

    def deploy_v2_script(
        self,
        instance_name: str,
        script: str,
        script_config: Optional[str] = None,
        credentials_profile: str = "master_account",
        image: str = "hummingbot/hummingbot:latest",
    ) -> Dict[str, Any]:
        """Deploy a V2 script strategy."""
        payload = {
            "instance_name": instance_name,
            "script": script,
            "credentials_profile": credentials_profile,
            "image": image,
        }
        if script_config:
            payload["script_config"] = script_config
        return self._request("POST", "/bot-orchestration/deploy-v2-script", json=payload)

    # ==================== Docker ====================

    def is_docker_running(self) -> Dict[str, Any]:
        """Check if Docker daemon is running."""
        return self._request("GET", "/docker/running")

    def get_active_containers(self, name_filter: Optional[str] = None) -> List[Dict]:
        """Get all active Docker containers."""
        params = {}
        if name_filter:
            params["name_filter"] = name_filter
        return self._request("GET", "/docker/active-containers", params=params)

    def get_exited_containers(self, name_filter: Optional[str] = None) -> List[Dict]:
        """Get all exited Docker containers."""
        params = {}
        if name_filter:
            params["name_filter"] = name_filter
        return self._request("GET", "/docker/exited-containers", params=params)

    def stop_container(self, container_name: str) -> Dict[str, Any]:
        """Stop a Docker container."""
        return self._request("POST", f"/docker/stop-container/{container_name}")

    def start_container(self, container_name: str) -> Dict[str, Any]:
        """Start a Docker container."""
        return self._request("POST", f"/docker/start-container/{container_name}")

    def remove_container(
        self,
        container_name: str,
        archive_locally: bool = True,
        s3_bucket: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove a container and archive its data."""
        params = {"archive_locally": archive_locally}
        if s3_bucket:
            params["s3_bucket"] = s3_bucket
        return self._request(
            "POST",
            f"/docker/remove-container/{container_name}",
            params=params,
        )

    # ==================== Scripts ====================

    def list_scripts(self) -> List[str]:
        """List all available scripts."""
        return self._request("GET", "/scripts/")

    def get_script(self, script_name: str) -> Dict[str, str]:
        """Get script content by name."""
        return self._request("GET", f"/scripts/{script_name}")

    def list_script_configs(self) -> List[Dict]:
        """List all script configurations."""
        return self._request("GET", "/scripts/configs/")

    def get_script_config(self, config_name: str) -> Dict[str, Any]:
        """Get script configuration by name."""
        return self._request("GET", f"/scripts/configs/{config_name}")

    def save_script_config(self, config_name: str, config: Dict[str, Any]) -> Dict:
        """Create or update script configuration."""
        return self._request("POST", f"/scripts/configs/{config_name}", json=config)

    def delete_script_config(self, config_name: str) -> Dict:
        """Delete script configuration."""
        return self._request("DELETE", f"/scripts/configs/{config_name}")

    # ==================== Gateway ====================

    def get_gateway_status(self) -> Dict[str, Any]:
        """Get Gateway status."""
        return self._request("GET", "/gateway/status")

    def get_gateway_connectors(self) -> Dict[str, Any]:
        """Get available Gateway connectors."""
        return self._request("GET", "/gateway/connectors")

    # ==================== Health Check ====================

    def health_check(self) -> Dict[str, Any]:
        """Check API health."""
        return self._request("GET", "/")

    def is_healthy(self) -> bool:
        """Check if API is accessible."""
        try:
            result = self.health_check()
            return result.get("status") == "running"
        except Exception:
            return False
