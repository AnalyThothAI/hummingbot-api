"""
Streamlit utilities for LP Dashboard.

Following the design patterns from official Hummingbot Dashboard.
"""
import atexit
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st

from api.client import APIConfig, LPDashboardAPI
from utils.config import get_config


# ==================== Cached Data Functions ====================
# Following official dashboard pattern: use @st.cache_data for API calls
# with appropriate TTL to reduce redundant requests

@st.cache_data(ttl=60, show_spinner=False)
def cached_get_bot_runs(limit: int = 50) -> List[Dict]:
    """Get bot runs with caching."""
    try:
        api = get_backend_api_client()
        response = api.get_bot_runs(limit=limit)
        return response.get("data", [])
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def cached_get_all_bots_status() -> Dict[str, Any]:
    """Get all bots status with caching."""
    try:
        api = get_backend_api_client()
        response = api.get_all_bots_status()
        return response.get("data", {}) if response.get("status") == "success" else {}
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def cached_get_active_containers(name_filter: str = "hummingbot") -> List[Dict]:
    """Get active containers with caching."""
    try:
        api = get_backend_api_client()
        return api.get_active_containers(name_filter=name_filter)
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def cached_list_script_configs() -> List[Dict]:
    """Get script configs with caching."""
    try:
        api = get_backend_api_client()
        return api.list_script_configs()
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def cached_get_gateway_status() -> Dict[str, Any]:
    """Get gateway status with caching."""
    try:
        api = get_backend_api_client()
        return api.get_gateway_status()
    except Exception:
        return {"running": False}


@st.cache_data(ttl=30, show_spinner=False)
def cached_get_mqtt_status() -> Dict[str, Any]:
    """Get MQTT status with caching."""
    try:
        api = get_backend_api_client()
        response = api.get_mqtt_status()
        return response.get("data", {})
    except Exception:
        return {"mqtt_connected": False, "discovered_bots": []}


def clear_all_caches():
    """Clear all cached data - useful after mutations."""
    cached_get_bot_runs.clear()
    cached_get_all_bots_status.clear()
    cached_get_active_containers.clear()
    cached_list_script_configs.clear()
    cached_get_gateway_status.clear()
    cached_get_mqtt_status.clear()


# ==================== State Management ====================
# Centralized state initialization following official dashboard patterns

def init_page_state(page_name: str, defaults: Dict[str, Any] = None):
    """Initialize page-specific session state with defaults.

    Args:
        page_name: Unique identifier for the page
        defaults: Dictionary of default values for state variables
    """
    if defaults is None:
        defaults = {}

    # Mark page as initialized
    init_key = f"{page_name}_initialized"
    if init_key not in st.session_state:
        st.session_state[init_key] = True

        # Set all defaults
        for key, value in defaults.items():
            state_key = f"{page_name}_{key}"
            if state_key not in st.session_state:
                st.session_state[state_key] = value


def get_page_state(page_name: str, key: str, default: Any = None) -> Any:
    """Get a page-specific state value."""
    state_key = f"{page_name}_{key}"
    return st.session_state.get(state_key, default)


def set_page_state(page_name: str, key: str, value: Any):
    """Set a page-specific state value."""
    state_key = f"{page_name}_{key}"
    st.session_state[state_key] = value


def initialize_st_page(
    title: Optional[str] = None,
    icon: str = "📊",
    layout: str = "wide",
    initial_sidebar_state: str = "expanded",
    show_readme: bool = False,
) -> None:
    """Initialize a Streamlit page with standard configuration."""
    st.set_page_config(
        page_title=title or "LP Dashboard",
        page_icon=icon,
        layout=layout,
        initial_sidebar_state=initial_sidebar_state,
    )

    if title:
        st.title(title)


def get_backend_api_client() -> LPDashboardAPI:
    """Get or create backend API client with connection management."""
    if "api_client" not in st.session_state or st.session_state.api_client is None:
        try:
            config = get_config()
            api_config = APIConfig(
                base_url=config.api_url,
                username=config.api_username,
                password=config.api_password,
                timeout=30.0,
            )
            client = LPDashboardAPI(api_config)

            def cleanup():
                try:
                    if "api_client" in st.session_state and st.session_state.api_client is not None:
                        st.session_state.api_client.close()
                        st.session_state.api_client = None
                except Exception:
                    pass

            atexit.register(cleanup)
            st.session_state.api_client = client

        except Exception as e:
            st.error(f"Failed to initialize API client: {e}")
            st.stop()

    return st.session_state.api_client


def main_page() -> List:
    """Get main landing page."""
    return [
        st.Page("pages/landing.py", title="LP Dashboard", icon="📊", url_path="landing", default=True),
    ]


def lp_management_pages() -> Dict[str, List]:
    """Get LP strategy management pages."""
    return {
        "Bot Orchestration": [
            st.Page("pages/1_Overview.py", title="Instances", icon="🦅", url_path="instances"),
            st.Page("pages/2_Deploy.py", title="Deploy", icon="🚀", url_path="deploy"),
            st.Page("pages/3_Monitor.py", title="Monitor", icon="📈", url_path="monitor"),
        ],
        "Configuration": [
            st.Page("pages/4_Config.py", title="Script Configs", icon="⚙️", url_path="config"),
            st.Page("pages/5_Logs.py", title="Logs", icon="📋", url_path="logs"),
        ],
    }


def auth_system() -> Dict[str, Any]:
    """Implement authentication system for LP Dashboard.

    Returns:
        Dictionary of page configurations based on auth state
    """
    auth_enabled = os.getenv("AUTH_SYSTEM_ENABLED", "false").lower() == "true"

    if not auth_enabled:
        return {
            "Main": main_page(),
            **lp_management_pages(),
        }

    # TODO: Implement full authentication if needed
    return {
        "Main": main_page(),
        **lp_management_pages(),
    }


def download_csv_button(df, filename: str, key: str) -> None:
    """Create a download button for DataFrame as CSV."""
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download CSV",
        data=csv,
        file_name=f"{filename}.csv",
        mime="text/csv",
        key=key,
    )


def format_currency(value: float, symbol: str = "$", decimals: int = 2) -> str:
    """Format a value as currency."""
    if value >= 0:
        return f"{symbol}{value:,.{decimals}f}"
    else:
        return f"-{symbol}{abs(value):,.{decimals}f}"


def format_percentage(value: float, decimals: int = 2, with_sign: bool = True) -> str:
    """Format a value as percentage."""
    pct = value * 100
    if with_sign and pct >= 0:
        return f"+{pct:.{decimals}f}%"
    return f"{pct:.{decimals}f}%"


# ==================== UI Components ====================
# Reusable UI patterns following official dashboard

def status_badge(status: str, size: str = "normal") -> str:
    """Generate status badge HTML.

    Args:
        status: One of 'running', 'stopped', 'error', 'unknown'
        size: 'small' or 'normal'
    """
    colors = {
        "running": ("#4CAF50", "🟢"),
        "stopped": ("#FFA500", "🟡"),
        "error": ("#f44336", "🔴"),
        "archived": ("#9E9E9E", "⚪"),
        "unknown": ("#9E9E9E", "❓"),
    }
    color, icon = colors.get(status.lower(), colors["unknown"])
    return f"{icon} {status.title()}"


def show_error_with_details(message: str, error: Exception, expanded: bool = False):
    """Show error message with expandable details."""
    st.error(message)
    with st.expander("Error Details", expanded=expanded):
        st.code(str(error))


def confirm_action(action_name: str, key: str) -> bool:
    """Two-step confirmation pattern for destructive actions.

    Returns True if action is confirmed.
    """
    confirm_key = f"confirm_{key}"

    if st.session_state.get(confirm_key):
        col1, col2 = st.columns(2)
        with col1:
            if st.button(f"✅ Yes, {action_name}", key=f"yes_{key}", type="primary"):
                st.session_state[confirm_key] = False
                return True
        with col2:
            if st.button("❌ Cancel", key=f"cancel_{key}"):
                st.session_state[confirm_key] = False
                st.rerun()
        return False
    return False


def action_with_feedback(action_func, success_msg: str, error_msg: str, rerun: bool = True):
    """Execute action with loading spinner and feedback.

    Args:
        action_func: Callable to execute
        success_msg: Message to show on success
        error_msg: Message prefix on error
        rerun: Whether to rerun page on success
    """
    import time

    with st.spinner("Processing..."):
        try:
            result = action_func()
            st.success(success_msg)
            clear_all_caches()  # Clear caches after mutation
            if rerun:
                time.sleep(0.5)
                st.rerun()
            return result
        except Exception as e:
            show_error_with_details(f"{error_msg}: {e}", e)
