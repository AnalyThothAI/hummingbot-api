from __future__ import annotations

import os
from typing import Dict, Optional


HBOT_MARKET_DATA_PROVIDER_CONTAINER_PATH = "/home/hummingbot/hummingbot/data_feed/market_data_provider.py"
HBOT_EXECUTOR_BASE_CONTAINER_PATH = "/home/hummingbot/hummingbot/strategy_v2/executors/executor_base.py"
HBOT_GATEWAY_LP_CONTAINER_PATH = "/home/hummingbot/hummingbot/connector/gateway/gateway_lp.py"


def build_bot_core_override_volumes(host_project_root: Optional[str]) -> Dict[str, Dict[str, str]]:
    """
    Build read-only bind mounts for bot containers to override specific Hummingbot core files.

    We need this because bot containers run a pre-built Hummingbot package image, but the
    orchestrator (this repo) may need to patch a small compatibility bug without rebuilding
    the full bot image. Using a single-file bind mount keeps the change minimal and isolated.
    """
    if not host_project_root:
        return {}

    market_data_provider_source = os.path.abspath(
        os.path.join(
            host_project_root,
            "hummingbot",
            "hummingbot",
            "data_feed",
            "market_data_provider.py",
        )
    )
    executor_base_source = os.path.abspath(
        os.path.join(
            host_project_root,
            "hummingbot",
            "hummingbot",
            "strategy_v2",
            "executors",
            "executor_base.py",
        )
    )
    gateway_lp_source = os.path.abspath(
        os.path.join(
            host_project_root,
            "hummingbot",
            "hummingbot",
            "connector",
            "gateway",
            "gateway_lp.py",
        )
    )

    volumes = {
        market_data_provider_source: {
            "bind": HBOT_MARKET_DATA_PROVIDER_CONTAINER_PATH,
            "mode": "ro",
        }
    }
    # Optional: only mount when present in the repo checkout.
    if os.path.exists(executor_base_source):
        volumes[executor_base_source] = {
            "bind": HBOT_EXECUTOR_BASE_CONTAINER_PATH,
            "mode": "ro",
        }
    if os.path.exists(gateway_lp_source):
        volumes[gateway_lp_source] = {
            "bind": HBOT_GATEWAY_LP_CONTAINER_PATH,
            "mode": "ro",
        }
    return volumes
