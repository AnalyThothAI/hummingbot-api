"""Sidebar component for LP Dashboard."""
import streamlit as st
from typing import Optional
from ..api.client import LPDashboardAPI, APIConfig
from ..api.models import MQTTStatus
from ..utils.config import get_config


def get_api_client() -> LPDashboardAPI:
    """Get or create API client from session state."""
    if "api_client" not in st.session_state:
        config = get_config()
        api_config = APIConfig(
            base_url=config.api_url,
            username=config.api_username,
            password=config.api_password,
        )
        st.session_state.api_client = LPDashboardAPI(api_config)
    return st.session_state.api_client


def render_sidebar():
    """Render the sidebar component."""
    with st.sidebar:
        st.title("LP Dashboard")
        st.markdown("---")

        # API Connection Status
        render_connection_status()

        st.markdown("---")

        # Quick Links
        render_quick_links()

        st.markdown("---")

        # Settings
        render_settings()


def render_connection_status():
    """Render API and MQTT connection status."""
    st.subheader("Connection Status")

    api = get_api_client()

    # API Status
    try:
        is_healthy = api.is_healthy()
        if is_healthy:
            st.markdown(":white_check_mark: **API**: Connected")
        else:
            st.markdown(":x: **API**: Disconnected")
    except Exception:
        st.markdown(":x: **API**: Error")

    # MQTT Status
    try:
        mqtt_response = api.get_mqtt_status()
        mqtt_status = MQTTStatus.from_api_response(mqtt_response)
        if mqtt_status.connected:
            st.markdown(":white_check_mark: **MQTT**: Connected")
            st.caption(f"Host: {mqtt_status.broker_host}:{mqtt_status.broker_port}")
        else:
            st.markdown(":x: **MQTT**: Disconnected")
    except Exception:
        st.markdown(":x: **MQTT**: Error")

    # Gateway Status
    try:
        gateway_response = api.get_gateway_status()
        # API returns {"running": bool, "container_id": str, "port": int, ...}
        if gateway_response.get("running"):
            st.markdown(":white_check_mark: **Gateway**: Online")
        else:
            st.markdown(":orange_circle: **Gateway**: Offline")
    except Exception:
        st.markdown(":x: **Gateway**: Error")


def render_quick_links():
    """Render quick navigation links."""
    st.subheader("Quick Links")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Refresh", use_container_width=True):
            st.rerun()
    with col2:
        if st.button("Clear Cache", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


def render_settings():
    """Render settings section."""
    with st.expander("Settings"):
        config = get_config()

        # Auto-refresh toggle
        auto_refresh = st.checkbox(
            "Auto Refresh",
            value=st.session_state.get("auto_refresh", False),
            key="auto_refresh_toggle",
        )
        st.session_state.auto_refresh = auto_refresh

        # Refresh interval
        if auto_refresh:
            interval = st.slider(
                "Refresh Interval (seconds)",
                min_value=1,
                max_value=60,
                value=st.session_state.get("refresh_interval", config.refresh_interval),
                key="refresh_interval_slider",
            )
            st.session_state.refresh_interval = interval

        # API URL display
        st.text_input(
            "API URL",
            value=config.api_url,
            disabled=True,
            key="api_url_display",
        )


def render_bot_summary():
    """Render a quick summary of active bots."""
    api = get_api_client()

    try:
        status_response = api.get_all_bots_status()
        bots_data = status_response.get("data", {})

        running_count = sum(
            1 for bot in bots_data.values()
            if isinstance(bot, dict) and bot.get("status") == "running"
        )
        total_count = len(bots_data)

        st.metric("Active Bots", f"{running_count}/{total_count}")
    except Exception:
        st.metric("Active Bots", "Error")


def auto_refresh_handler():
    """Handle auto-refresh logic."""
    import time

    if st.session_state.get("auto_refresh", False):
        interval = st.session_state.get("refresh_interval", 5)
        time.sleep(interval)
        st.rerun()
