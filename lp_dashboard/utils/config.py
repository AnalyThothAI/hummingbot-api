"""Configuration management for LP Dashboard."""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class DashboardConfig:
    """Dashboard configuration settings."""
    api_url: str
    api_username: str
    api_password: str
    refresh_interval: int = 5  # seconds
    page_title: str = "LP Dashboard"
    page_icon: str = "chart_with_upwards_trend"

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        """Create configuration from environment variables."""
        return cls(
            api_url=os.getenv("API_URL", "http://localhost:8000"),
            api_username=os.getenv("API_USERNAME", "admin"),
            api_password=os.getenv("API_PASSWORD", "admin"),
            refresh_interval=int(os.getenv("REFRESH_INTERVAL", "5")),
            page_title=os.getenv("PAGE_TITLE", "LP Dashboard"),
            page_icon=os.getenv("PAGE_ICON", "chart_with_upwards_trend"),
        )


def get_config() -> DashboardConfig:
    """Get dashboard configuration."""
    return DashboardConfig.from_env()


# Common Docker images
DEFAULT_IMAGES = [
    "hummingbot/hummingbot:latest",
    "hummingbot/hummingbot:development",
]

# Common scripts for LP strategies
LP_SCRIPTS = [
    "gateway_lp_guarded",
    "v2_with_controllers",
]

# Status colors for UI
STATUS_COLORS = {
    "running": "green",
    "stopped": "gray",
    "error": "red",
    "starting": "blue",
    "stopping": "orange",
    "unknown": "gray",
}

# Status emojis for UI
STATUS_EMOJIS = {
    "running": "white_check_mark",
    "stopped": "red_circle",
    "error": "x",
    "starting": "hourglass_flowing_sand",
    "stopping": "hourglass_flowing_sand",
    "unknown": "question",
}
