"""Helper utilities for LP Dashboard."""
import streamlit as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import yaml


def format_timestamp(ts: Optional[datetime]) -> str:
    """Format a timestamp for display."""
    if ts is None:
        return "N/A"
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def format_duration(start: Optional[datetime], end: Optional[datetime] = None) -> str:
    """Format duration between two timestamps."""
    if start is None:
        return "N/A"

    if end is None:
        end = datetime.now()

    delta = end - start

    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


def format_number(value: Optional[float], decimals: int = 2, prefix: str = "") -> str:
    """Format a number for display."""
    if value is None:
        return "N/A"
    return f"{prefix}{value:,.{decimals}f}"


def format_pnl(value: Optional[float], decimals: int = 2) -> str:
    """Format PnL with color indicator."""
    if value is None:
        return "N/A"

    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.{decimals}f}"


def get_pnl_color(value: Optional[float]) -> str:
    """Get color for PnL display."""
    if value is None:
        return "gray"
    return "green" if value >= 0 else "red"


def yaml_to_dict(yaml_str: str) -> Dict[str, Any]:
    """Parse YAML string to dictionary."""
    try:
        return yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")


def dict_to_yaml(data: Dict[str, Any]) -> str:
    """Convert dictionary to YAML string."""
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def get_status_icon(status: str) -> str:
    """Get icon for status display."""
    icons = {
        "running": "check_circle",
        "stopped": "cancel",
        "error": "error",
        "starting": "hourglass_empty",
        "stopping": "hourglass_top",
        "unknown": "help",
        "connected": "link",
        "disconnected": "link_off",
    }
    return icons.get(status.lower(), "help")


def truncate_string(s: str, max_length: int = 50) -> str:
    """Truncate a string with ellipsis."""
    if len(s) <= max_length:
        return s
    return s[:max_length - 3] + "..."


def safe_get(data: Dict, *keys, default=None) -> Any:
    """Safely get nested dictionary value."""
    result = data
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key, default)
        else:
            return default
    return result


def create_bot_name(prefix: str, suffix: Optional[str] = None) -> str:
    """Create a unique bot name with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if suffix:
        return f"{prefix}-{suffix}-{timestamp}"
    return f"{prefix}-{timestamp}"


def parse_trading_pair(pair: str) -> tuple:
    """Parse trading pair into base and quote."""
    separators = ["-", "/", "_"]
    for sep in separators:
        if sep in pair:
            parts = pair.split(sep)
            if len(parts) == 2:
                return parts[0], parts[1]
    return pair, ""


@st.cache_data(ttl=60)
def cached_api_call(func, *args, **kwargs):
    """Cache API call results for 60 seconds."""
    return func(*args, **kwargs)


def init_session_state(key: str, default: Any) -> Any:
    """Initialize session state with default value."""
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def show_success(message: str):
    """Show success message."""
    st.success(message)


def show_error(message: str):
    """Show error message."""
    st.error(message)


def show_warning(message: str):
    """Show warning message."""
    st.warning(message)


def show_info(message: str):
    """Show info message."""
    st.info(message)
