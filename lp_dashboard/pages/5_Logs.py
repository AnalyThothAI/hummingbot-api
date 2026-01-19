"""Logs page - View strategy logs (Real Data)."""
from datetime import datetime

import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="📋", show_readme=False)

api = get_backend_api_client()


def get_bot_list():
    """Get list of available bots from MQTT."""
    try:
        mqtt_response = api.get_mqtt_status()
        mqtt_data = mqtt_response.get("data", {})
        return mqtt_data.get("discovered_bots", [])
    except Exception:
        return []


def get_container_list():
    """Get list of available containers."""
    try:
        containers = api.get_active_containers(name_filter="hummingbot")
        container_names = [c.get("name", "") for c in containers if c.get("name")]

        exited = api.get_exited_containers(name_filter="hummingbot")
        exited_names = [c.get("name", "") for c in exited if c.get("name")]

        all_containers = list(set(container_names + exited_names))
        all_containers.sort()
        return all_containers
    except Exception:
        return []


# Page Header
st.title("📋 Logs (Real Data)")
st.subheader("View strategy and Gateway logs")

# Log Source Selection
with st.container(border=True):
    st.info("🎯 **Log Source Selection:** Choose what logs to view")

    log_source = st.radio(
        "Log Source",
        options=["Gateway Logs", "Bot Logs"],
        horizontal=True,
        label_visibility="collapsed",
    )

# Gateway Logs Section
if log_source == "Gateway Logs":
    with st.container(border=True):
        st.success("🌐 **Gateway Logs:** Real-time logs from Hummingbot Gateway")

        col1, col2 = st.columns([1, 1])

        with col1:
            lines = st.selectbox(
                "Lines",
                options=[50, 100, 200, 500],
                index=1,
                help="Number of log lines to display",
            )

        with col2:
            if st.button("🔄 Refresh", use_container_width=True, type="primary"):
                st.rerun()

    # Log Filters
    with st.container(border=True):
        st.warning("🔍 **Log Filters:** Filter and search logs")

        col1, col2 = st.columns([2, 1])

        with col1:
            search = st.text_input(
                "🔎 Search",
                placeholder="Filter logs by keyword...",
            )

        with col2:
            level = st.selectbox(
                "📊 Level",
                options=["ALL", "debug", "info", "warning", "error"],
            )

    # Log Viewer
    with st.container(border=True):
        st.info("📜 **Log Viewer:** Gateway logs")

        try:
            logs_response = api.get_gateway_logs(lines=lines)
            logs_text = logs_response.get("logs", "")

            if logs_text:
                # Split into lines
                log_lines = logs_text.strip().split("\n")

                # Apply level filter
                if level != "ALL":
                    log_lines = [line for line in log_lines if f"| {level} |" in line.lower()]

                # Apply search filter
                if search:
                    log_lines = [line for line in log_lines if search.lower() in line.lower()]

                if log_lines:
                    # Display logs
                    log_display = "\n".join(log_lines[-lines:])
                    st.code(log_display, language="log")

                    # Log Statistics
                    info_count = sum(1 for line in log_lines if "| info |" in line.lower())
                    warn_count = sum(1 for line in log_lines if "| warning |" in line.lower() or "| warn |" in line.lower())
                    error_count = sum(1 for line in log_lines if "| error |" in line.lower())
                    debug_count = sum(1 for line in log_lines if "| debug |" in line.lower())

                    st.markdown("**Log Statistics:**")
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("ℹ️ INFO", info_count)
                    with col2:
                        st.metric("⚠️ WARNING", warn_count)
                    with col3:
                        st.metric("❌ ERROR", error_count)
                    with col4:
                        st.metric("🔧 DEBUG", debug_count)
                else:
                    st.info("No logs match the current filters")
            else:
                st.info("No Gateway logs available")

        except Exception as e:
            st.error(f"Error fetching Gateway logs: {e}")

# Bot Logs Section
else:
    bot_list = get_bot_list()

    with st.container(border=True):
        st.success("🤖 **Bot Logs:** Logs from connected bots via MQTT")

        col1, col2 = st.columns([2, 1])

        with col1:
            if bot_list:
                selected_bot = st.selectbox(
                    "Select Bot",
                    options=bot_list,
                    label_visibility="collapsed",
                )
            else:
                selected_bot = None
                st.warning("No bots connected via MQTT")

        with col2:
            if st.button("🔄 Refresh", use_container_width=True, type="primary"):
                st.rerun()

    if not selected_bot:
        st.warning("⚠️ No bots available. Start a bot to view its logs.")
        st.stop()

    # Get bot status which includes logs
    try:
        status_response = api.get_bot_status(selected_bot)
        bot_data = status_response.get("data", {})

        general_logs = bot_data.get("general_logs", [])
        error_logs = bot_data.get("error_logs", [])

        # Log Filters
        with st.container(border=True):
            st.warning("🔍 **Log Filters:** Filter and search logs")

            col1, col2 = st.columns([2, 1])

            with col1:
                search = st.text_input(
                    "🔎 Search",
                    placeholder="Filter logs by keyword...",
                    key="bot_search",
                )

            with col2:
                log_type = st.selectbox(
                    "📊 Log Type",
                    options=["All Logs", "General Logs", "Error Logs"],
                )

        # Log Viewer
        with st.container(border=True):
            st.info(f"📜 **Log Viewer:** {selected_bot}")

            # Combine and format logs
            all_logs = []

            if log_type in ["All Logs", "General Logs"]:
                for log in general_logs:
                    timestamp = datetime.fromtimestamp(log.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M:%S")
                    level = log.get("level_name", "INFO")
                    msg = log.get("msg", "")
                    all_logs.append(f"[{timestamp}] {level} - {msg}")

            if log_type in ["All Logs", "Error Logs"]:
                for log in error_logs:
                    timestamp = datetime.fromtimestamp(log.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M:%S")
                    level = log.get("level_name", "ERROR")
                    msg = log.get("msg", "")
                    all_logs.append(f"[{timestamp}] {level} - {msg}")

            # Sort by timestamp
            all_logs.sort()

            # Apply search filter
            if search:
                all_logs = [log for log in all_logs if search.lower() in log.lower()]

            if all_logs:
                log_display = "\n".join(all_logs[-200:])  # Show last 200 lines
                st.code(log_display, language="log")

                # Statistics
                st.markdown("**Log Statistics:**")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("📝 General Logs", len(general_logs))
                with col2:
                    st.metric("❌ Error Logs", len(error_logs))
                with col3:
                    st.metric("📊 Total", len(general_logs) + len(error_logs))
            else:
                st.info("No logs available for this bot")

    except Exception as e:
        st.error(f"Error fetching bot logs: {e}")

# Docker Command Hint
with st.expander("💻 View Live Logs via Docker (Terminal Command)"):
    container_list = get_container_list()
    if container_list:
        selected_container = st.selectbox("Select Container", options=container_list)
        st.code(f"docker logs -f --tail 100 {selected_container}", language="bash")
    else:
        st.info("No containers available")
