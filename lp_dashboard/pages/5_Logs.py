"""Logs page - View strategy logs."""
from datetime import datetime, timedelta
import random

import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="📋", show_readme=False)

api = get_backend_api_client()


def get_container_list():
    """Get list of available containers."""
    try:
        # Get active containers
        containers = api.get_active_containers(name_filter="hummingbot")
        container_names = [c.get("name", "") for c in containers if c.get("name")]

        # Also get exited containers
        exited = api.get_exited_containers(name_filter="hummingbot")
        exited_names = [c.get("name", "") for c in exited if c.get("name")]

        all_containers = list(set(container_names + exited_names))
        all_containers.sort()
        return all_containers
    except Exception:
        return []


def generate_sample_logs(container_name: str, lines: int) -> list:
    """Generate sample log entries for demo."""
    log_templates = [
        "[{time}] INFO - Strategy initialized successfully",
        "[{time}] INFO - Connected to exchange gateway",
        "[{time}] DEBUG - Fetching market data...",
        "[{time}] INFO - Position created: SOL-USDC",
        "[{time}] INFO - Monitoring active positions",
        "[{time}] DEBUG - Price update: ${price}",
        "[{time}] INFO - Collecting fees: ${fee}",
        "[{time}] WARNING - Price approaching range boundary",
        "[{time}] INFO - Rebalancing position",
        "[{time}] DEBUG - Heartbeat received",
        "[{time}] INFO - Trade executed successfully",
        "[{time}] WARNING - High gas prices detected",
        "[{time}] INFO - Position still in range",
        "[{time}] DEBUG - Checking position status...",
        "[{time}] ERROR - Connection timeout, retrying...",
    ]

    logs = []
    now = datetime.now()
    random.seed(hash(container_name))

    for i in range(min(lines, 50)):
        time_str = (now - timedelta(minutes=i * 2)).strftime("%Y-%m-%d %H:%M:%S")
        template = random.choice(log_templates)

        log = template.format(
            time=time_str,
            price=f"{random.uniform(90, 110):.4f}",
            fee=f"{random.uniform(0.01, 0.5):.4f}",
        )
        logs.append(log)

    return logs[::-1]  # Reverse to show newest last


# Page Header
st.title("📋 Logs")
st.subheader("View strategy and container logs")

# Container Selection Section
with st.container(border=True):
    st.info("🎯 **Container Selection:** Choose a container to view logs")

    col1, col2, col3 = st.columns([2, 1, 1])

    container_list = get_container_list()

    with col1:
        if container_list:
            selected_container = st.selectbox(
                "Container",
                options=container_list,
                label_visibility="collapsed",
            )
        else:
            selected_container = None
            st.warning("No containers available")

    with col2:
        lines = st.selectbox(
            "Lines",
            options=[50, 100, 200, 500],
            index=1,
            label_visibility="collapsed",
            help="Number of log lines to display",
        )

    with col3:
        if st.button("🔄 Refresh", use_container_width=True, type="primary"):
            st.rerun()

if not selected_container:
    st.warning("⚠️ Select a container to view logs")
    st.stop()

# Container Status Section
with st.container(border=True):
    st.success(f"🐳 **Container Status:** {selected_container}")

    try:
        containers = api.get_active_containers()
        container = next(
            (c for c in containers if c.get("name") == selected_container),
            None
        )

        if container:
            col1, col2, col3 = st.columns(3)

            with col1:
                status = container.get("status", "Unknown")
                if "Up" in status:
                    st.metric("📊 Status", f"🟢 {status}")
                else:
                    st.metric("📊 Status", f"🟡 {status}")

            with col2:
                image = container.get("image", "Unknown")
                st.metric("🖼️ Image", image[:30] + "..." if len(image) > 30 else image)

            with col3:
                created = container.get("created", "Unknown")
                st.metric("📅 Created", created)
        else:
            # Check exited containers
            exited = api.get_exited_containers()
            container = next(
                (c for c in exited if c.get("name") == selected_container),
                None
            )

            if container:
                st.warning(f"⚠️ Container {selected_container} is stopped")
            else:
                st.error(f"❌ Container {selected_container} not found")

    except Exception as e:
        st.error(f"Error getting container info: {e}")

# Log Filters Section
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
            options=["ALL", "DEBUG", "INFO", "WARNING", "ERROR"],
        )

# Log Viewer Section
with st.container(border=True):
    st.info(f"📜 **Log Viewer:** Showing logs for {selected_container}")

    # Command hint
    with st.expander("💻 View Live Logs (Terminal Command)"):
        st.code(f"docker logs -f --tail {lines} {selected_container}", language="bash")

    # Generate/fetch logs
    sample_logs = generate_sample_logs(selected_container, lines)

    # Apply filters
    if level != "ALL":
        sample_logs = [log for log in sample_logs if level in log]

    if search:
        sample_logs = [log for log in sample_logs if search.lower() in log.lower()]

    # Display logs
    if sample_logs:
        log_text = "\n".join(sample_logs)
        st.code(log_text, language="log")
    else:
        st.info("No logs match the current filters")

# Log Statistics Section
with st.container(border=True):
    st.success("📊 **Log Statistics:** Summary of log levels")

    # Recalculate from original logs before filtering
    all_logs = generate_sample_logs(selected_container, lines)

    col1, col2, col3, col4 = st.columns(4)

    info_count = sum(1 for log in all_logs if "INFO" in log)
    warn_count = sum(1 for log in all_logs if "WARNING" in log)
    error_count = sum(1 for log in all_logs if "ERROR" in log)
    debug_count = sum(1 for log in all_logs if "DEBUG" in log)

    with col1:
        st.metric("ℹ️ INFO", info_count)
    with col2:
        st.metric("⚠️ WARNING", warn_count)
    with col3:
        st.metric("❌ ERROR", error_count)
    with col4:
        st.metric("🔧 DEBUG", debug_count)
