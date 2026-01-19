"""
Streamlit utilities for LP Dashboard.

Following the design patterns from official Hummingbot Dashboard.
"""
import atexit
import os
from typing import Any, Dict, List, Optional

import streamlit as st

from api.client import APIConfig, LPDashboardAPI
from utils.config import get_config


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
