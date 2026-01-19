"""Data models for LP Dashboard."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
from enum import Enum


class BotStatus(str, Enum):
    """Bot running status."""
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    STARTING = "starting"
    STOPPING = "stopping"
    UNKNOWN = "unknown"


class DeploymentStatus(str, Enum):
    """Bot deployment status."""
    DEPLOYED = "deployed"
    FAILED = "failed"
    ARCHIVED = "archived"


@dataclass
class StrategyInfo:
    """Strategy information."""
    name: str
    status: BotStatus
    script: Optional[str] = None
    config: Optional[str] = None
    container_name: Optional[str] = None
    uptime: Optional[str] = None
    pnl: Optional[float] = None
    fees_collected: Optional[float] = None
    trading_pair: Optional[str] = None
    exchange: Optional[str] = None

    @classmethod
    def from_api_response(cls, name: str, data: Dict[str, Any]) -> "StrategyInfo":
        """Create StrategyInfo from API response."""
        status_str = data.get("status", "unknown")
        try:
            status = BotStatus(status_str.lower())
        except ValueError:
            status = BotStatus.UNKNOWN

        return cls(
            name=name,
            status=status,
            script=data.get("script"),
            config=data.get("config"),
            container_name=data.get("container_name"),
            uptime=data.get("uptime"),
            pnl=data.get("pnl"),
            fees_collected=data.get("fees_collected"),
            trading_pair=data.get("trading_pair"),
            exchange=data.get("exchange"),
        )


@dataclass
class BotRun:
    """Bot run record."""
    id: int
    bot_name: str
    instance_name: str
    deployed_at: Optional[datetime]
    stopped_at: Optional[datetime]
    strategy_type: str
    strategy_name: str
    config_name: Optional[str]
    account_name: str
    image_version: str
    deployment_status: str
    run_status: str
    deployment_config: Optional[Dict]
    final_status: Optional[Dict]
    error_message: Optional[str]

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "BotRun":
        """Create BotRun from API response."""
        deployed_at = None
        if data.get("deployed_at"):
            deployed_at = datetime.fromisoformat(data["deployed_at"])

        stopped_at = None
        if data.get("stopped_at"):
            stopped_at = datetime.fromisoformat(data["stopped_at"])

        return cls(
            id=data.get("id", 0),
            bot_name=data.get("bot_name", ""),
            instance_name=data.get("instance_name", ""),
            deployed_at=deployed_at,
            stopped_at=stopped_at,
            strategy_type=data.get("strategy_type", ""),
            strategy_name=data.get("strategy_name", ""),
            config_name=data.get("config_name"),
            account_name=data.get("account_name", ""),
            image_version=data.get("image_version", ""),
            deployment_status=data.get("deployment_status", ""),
            run_status=data.get("run_status", ""),
            deployment_config=data.get("deployment_config"),
            final_status=data.get("final_status"),
            error_message=data.get("error_message"),
        )


@dataclass
class ConfigInfo:
    """Script configuration info."""
    config_name: str
    script_file_name: str
    controllers_config: List[str] = field(default_factory=list)
    candles_config: List[Dict] = field(default_factory=list)
    markets: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "ConfigInfo":
        """Create ConfigInfo from API response."""
        return cls(
            config_name=data.get("config_name", ""),
            script_file_name=data.get("script_file_name", ""),
            controllers_config=data.get("controllers_config", []),
            candles_config=data.get("candles_config", []),
            markets=data.get("markets", {}),
            error=data.get("error"),
        )


@dataclass
class ContainerInfo:
    """Docker container information."""
    name: str
    status: str
    image: str
    created: Optional[str] = None
    ports: Optional[Dict] = None

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "ContainerInfo":
        """Create ContainerInfo from API response."""
        return cls(
            name=data.get("name", ""),
            status=data.get("status", ""),
            image=data.get("image", ""),
            created=data.get("created"),
            ports=data.get("ports"),
        )


@dataclass
class GatewayStatus:
    """Gateway connection status."""
    connected: bool
    url: str
    version: Optional[str] = None
    chains: List[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "GatewayStatus":
        """Create GatewayStatus from API response."""
        return cls(
            connected=data.get("connected", False),
            url=data.get("url", ""),
            version=data.get("version"),
            chains=data.get("chains", []),
        )


@dataclass
class MQTTStatus:
    """MQTT broker status."""
    connected: bool
    broker_host: str
    broker_port: int
    discovered_bots: List[str] = field(default_factory=list)
    active_bots: List[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "MQTTStatus":
        """Create MQTTStatus from API response."""
        inner_data = data.get("data", data)
        return cls(
            connected=inner_data.get("mqtt_connected", False),
            broker_host=inner_data.get("broker_host", ""),
            broker_port=inner_data.get("broker_port", 1883),
            discovered_bots=inner_data.get("discovered_bots", []),
            active_bots=inner_data.get("active_bots", []),
        )
