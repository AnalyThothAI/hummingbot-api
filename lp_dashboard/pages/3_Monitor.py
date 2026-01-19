"""Monitor page - Real-time strategy monitoring."""
import time
from datetime import datetime, timedelta
import random

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="📈", show_readme=False)

api = get_backend_api_client()

# Initialize session state
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = False

REFRESH_INTERVAL = 10


def get_bot_list():
    """Get list of available bots to monitor."""
    try:
        status_response = api.get_all_bots_status()
        bots = list(status_response.get("data", {}).keys())
    except Exception:
        bots = []

    try:
        containers = api.get_active_containers(name_filter="hummingbot")
        container_names = [c.get("name", "") for c in containers if c.get("name")]
        bots = list(set(bots + container_names))
    except Exception:
        pass

    return sorted(bots)


# Page Header
st.title("📈 Strategy Monitor")
st.subheader("Real-time monitoring of your LP strategy performance")

# Strategy Selection Section
with st.container(border=True):
    st.info("🎯 **Strategy Selection:** Choose a strategy to monitor")

    col1, col2, col3 = st.columns([2, 1, 1])

    bot_list = get_bot_list()

    with col1:
        if bot_list:
            default_bot = st.session_state.get("selected_bot")
            default_idx = 0
            if default_bot and default_bot in bot_list:
                default_idx = bot_list.index(default_bot)

            selected_bot = st.selectbox(
                "Select Strategy",
                options=bot_list,
                index=default_idx,
                label_visibility="collapsed",
            )
        else:
            selected_bot = None
            st.warning("No strategies available to monitor")

    with col2:
        refresh_label = "⏸️ Pause" if st.session_state.auto_refresh_enabled else "▶️ Auto-refresh"
        if st.button(refresh_label, use_container_width=True):
            st.session_state.auto_refresh_enabled = not st.session_state.auto_refresh_enabled
            st.rerun()

    with col3:
        if st.button("🔄 Refresh Now", use_container_width=True, type="primary"):
            st.rerun()

if not selected_bot:
    st.warning("⚠️ Deploy a strategy first to start monitoring.")
    if st.button("🚀 Go to Deploy", type="primary"):
        st.switch_page("pages/2_Deploy.py")
    st.stop()


# Load bot data
try:
    status_response = api.get_bot_status(selected_bot)
    bot_data = status_response.get("data", {})
except Exception as e:
    st.error(f"Error loading strategy data: {e}")
    bot_data = {}

status = bot_data.get("status", "unknown")


# Bot Status Section
with st.container(border=True):
    if status == "running":
        st.success(f"🤖 **{selected_bot}** - Running")
    elif status == "stopped":
        st.warning(f"🤖 **{selected_bot}** - Stopped")
    else:
        st.info(f"🤖 **{selected_bot}** - {status}")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        uptime = bot_data.get("uptime", "N/A")
        st.metric("⏱️ Uptime", uptime)

    with col2:
        script = bot_data.get("script", "N/A")
        st.metric("📜 Script", script[:15] + "..." if len(str(script)) > 15 else script)

    with col3:
        if st.session_state.auto_refresh_enabled:
            st.metric("🔄 Auto-refresh", f"{REFRESH_INTERVAL}s")
        else:
            st.metric("🔄 Auto-refresh", "Paused")

    with col4:
        if status == "running":
            if st.button("⏹️ Stop Bot", use_container_width=True):
                with st.spinner("Stopping..."):
                    try:
                        api.stop_and_archive_bot(selected_bot)
                        st.success("Bot stopped")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")


# Performance Metrics Section
@st.fragment(run_every=REFRESH_INTERVAL if st.session_state.auto_refresh_enabled else None)
def performance_section():
    """Fragment for performance metrics with auto-refresh."""
    with st.container(border=True):
        st.success("📊 **Performance Metrics:** Real-time PnL and trading statistics")

        performance = bot_data.get("performance", {})

        # Calculate totals from controller performance
        total_pnl = 0
        total_unrealized = 0
        total_volume = 0
        total_fees = 0

        for controller, inner_dict in performance.items():
            if isinstance(inner_dict, dict) and inner_dict.get("status") != "error":
                perf = inner_dict.get("performance", {})
                total_pnl += perf.get("global_pnl_quote", 0)
                total_unrealized += perf.get("unrealized_pnl_quote", 0)
                total_volume += perf.get("volume_traded", 0)
                total_fees += perf.get("fees_collected", 0)

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            delta_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric(
                "🏦 Total PnL",
                f"${total_pnl:.2f}",
                delta=f"${total_unrealized:.2f} unrealized",
                delta_color=delta_color,
            )

        with col2:
            st.metric("💰 Fees Collected", f"${total_fees:.4f}")

        with col3:
            pnl_pct = (total_pnl / total_volume * 100) if total_volume > 0 else 0
            st.metric("📈 PnL %", f"{pnl_pct:.2f}%")

        with col4:
            st.metric("💸 Volume Traded", f"${total_volume:.2f}")


performance_section()


# Charts Section
with st.container(border=True):
    st.info("📉 **Charts:** Visual representation of strategy performance")

    tab1, tab2 = st.tabs(["📈 PnL History", "🎯 Position Range"])

    with tab1:
        # Generate sample PnL history (replace with real data when available)
        now = datetime.now()
        timestamps = [now - timedelta(hours=i) for i in range(24, 0, -1)]
        random.seed(hash(selected_bot))
        pnl_values = []
        current = 0
        for _ in range(24):
            current += random.uniform(-10, 15)
            pnl_values.append(current)

        df = pd.DataFrame({
            "Time": timestamps,
            "PnL": pnl_values,
        })

        fig = px.area(
            df,
            x="Time",
            y="PnL",
            title="PnL History (24h)",
            color_discrete_sequence=["#00D26A"],
        )
        fig.update_layout(
            template="plotly_dark",
            height=400,
            margin=dict(l=20, r=20, t=50, b=20),
            xaxis_title="Time",
            yaxis_title="PnL (USD)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        position = bot_data.get("position", {})
        current_price = position.get("current_price", 100)
        lower_price = position.get("lower_price", 90)
        upper_price = position.get("upper_price", 110)

        # Simple position range visualization
        in_range = lower_price <= current_price <= upper_price

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("📉 Lower Bound", f"${lower_price:.4f}")

        with col2:
            if in_range:
                st.metric("💹 Current Price", f"${current_price:.4f}", delta="In Range")
            else:
                st.metric("💹 Current Price", f"${current_price:.4f}", delta="Out of Range", delta_color="inverse")

        with col3:
            st.metric("📈 Upper Bound", f"${upper_price:.4f}")

        # Progress bar showing position in range
        if upper_price > lower_price:
            range_pct = (current_price - lower_price) / (upper_price - lower_price)
            range_pct = max(0, min(1, range_pct))
            st.progress(range_pct, text=f"Position: {range_pct:.1%} through range")


# Controller Details Section
with st.container(border=True):
    st.warning("🎛️ **Controller Details:** Individual controller performance")

    performance = bot_data.get("performance", {})

    if performance:
        controllers_data = []
        for controller, inner_dict in performance.items():
            if isinstance(inner_dict, dict):
                if inner_dict.get("status") == "error":
                    controllers_data.append({
                        "Controller": controller,
                        "Status": "❌ Error",
                        "Realized PnL": "N/A",
                        "Unrealized PnL": "N/A",
                        "NET PnL": "N/A",
                        "Volume": "N/A",
                    })
                else:
                    perf = inner_dict.get("performance", {})
                    controllers_data.append({
                        "Controller": controller,
                        "Status": "✅ Active",
                        "Realized PnL": f"${perf.get('realized_pnl_quote', 0):.2f}",
                        "Unrealized PnL": f"${perf.get('unrealized_pnl_quote', 0):.2f}",
                        "NET PnL": f"${perf.get('global_pnl_quote', 0):.2f}",
                        "Volume": f"${perf.get('volume_traded', 0):.2f}",
                    })

        if controllers_data:
            st.dataframe(
                pd.DataFrame(controllers_data),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No controller data available")
    else:
        st.info("No performance data available")


# Auto-refresh indicator
if st.session_state.auto_refresh_enabled:
    st.caption(f"🔄 Auto-refreshing every {REFRESH_INTERVAL} seconds")
