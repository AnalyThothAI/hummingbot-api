from __future__ import annotations

import os
from typing import Dict, Optional


HBOT_MARKET_DATA_PROVIDER_CONTAINER_PATH = "/home/hummingbot/hummingbot/data_feed/market_data_provider.py"


def build_bot_core_override_volumes(host_project_root: Optional[str]) -> Dict[str, Dict[str, str]]:
    """
    Build read-only bind mounts for bot containers to override specific Hummingbot core files.

    We need this because bot containers run a pre-built Hummingbot package image, but the
    orchestrator (this repo) may need to patch a small compatibility bug without rebuilding
    the full bot image. Using a single-file bind mount keeps the change minimal and isolated.
    """
    if not host_project_root:
        return {}

    source = os.path.abspath(
        os.path.join(
            host_project_root,
            "hummingbot",
            "hummingbot",
            "data_feed",
            "market_data_provider.py",
        )
    )
    return {
        source: {
            "bind": HBOT_MARKET_DATA_PROVIDER_CONTAINER_PATH,
            "mode": "ro",
        }
    }

