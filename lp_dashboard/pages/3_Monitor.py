"""Monitor page - Real-time strategy monitoring."""
import time
from datetime import datetime, timedelta

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

    tab1, tab2 = st.tabs(["📈 PnL History (Real Data)", "🎯 Position Range (Real Data)"])

    with tab1:
        # Get real PnL history from portfolio history
        try:
            now = datetime.now()
            start_time = int((now - timedelta(hours=24)).timestamp() * 1000)
            end_time = int(now.timestamp() * 1000)

            history_response = api.get_portfolio_history(
                start_time=start_time,
                end_time=end_time,
                interval="15m",
                limit=96,  # 24 hours * 4 (15-min intervals)
            )

            history_data = history_response.get("data", [])

            if history_data and len(history_data) > 1:
                # Parse history data to calculate PnL changes
                timestamps = []
                pnl_values = []
                base_value = None

                for record in reversed(history_data):  # Reverse to get chronological order
                    ts = record.get("timestamp")
                    if ts:
                        timestamps.append(pd.to_datetime(ts))

                        # Calculate total value from all accounts
                        # Data structure: {"timestamp": ..., "state": {"account": {"connector": [{"value": ...}]}}}
                        total_value = 0
                        state = record.get("state", {})
                        for account_name, account_data in state.items():
                            if isinstance(account_data, dict):
                                for connector_name, tokens in account_data.items():
                                    if isinstance(tokens, list):
                                        for token_data in tokens:
                                            total_value += token_data.get("value", 0)

                        if base_value is None:
                            base_value = total_value

                        # PnL is the change from base value
                        pnl_values.append(total_value - base_value)

                if timestamps and pnl_values:
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
                else:
                    st.info("No PnL history data available yet.")
            else:
                st.info("No PnL history data available yet. The bot needs to run for a while to collect data.")
        except Exception as e:
            st.warning(f"Could not load PnL history: {e}")

    with tab2:
        # Get real CLMM positions from database
        try:
            positions_response = api.get_clmm_positions(limit=10)
            positions_data = positions_response.get("data", [])

            if positions_data:
                # Get the most recent position (first one, as they're sorted by created_at desc)
                latest_position = positions_data[0]

                current_price = latest_position.get("current_price", 0)
                lower_price = latest_position.get("lower_price", 0)
                upper_price = latest_position.get("upper_price", 0)
                in_range_status = latest_position.get("in_range", "UNKNOWN")
                trading_pair = latest_position.get("trading_pair", "Unknown")
                position_status = latest_position.get("status", "UNKNOWN")
                pnl_summary = latest_position.get("pnl_summary", {})

                # Display position info header
                status_emoji = "🟢" if position_status == "OPEN" else "🔴"
                range_emoji = "✅" if in_range_status == "IN_RANGE" else "⚠️"
                st.caption(f"{status_emoji} Position: {trading_pair} | {range_emoji} {in_range_status}")

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric("📉 Lower Bound", f"{lower_price:.8f}")

                with col2:
                    in_range = in_range_status == "IN_RANGE"
                    if in_range:
                        st.metric("💹 Current Price", f"{current_price:.8f}", delta="In Range")
                    else:
                        st.metric("💹 Current Price", f"{current_price:.8f}", delta="Out of Range", delta_color="inverse")

                with col3:
                    st.metric("📈 Upper Bound", f"{upper_price:.8f}")

                # Progress bar showing position in range
                if upper_price > lower_price:
                    range_pct = (current_price - lower_price) / (upper_price - lower_price)
                    range_pct = max(0, min(1, range_pct))
                    st.progress(range_pct, text=f"Position: {range_pct:.1%} through range")

                # PnL Summary
                if pnl_summary:
                    st.markdown("**PnL Summary:**")
                    pnl_col1, pnl_col2, pnl_col3, pnl_col4 = st.columns(4)
                    with pnl_col1:
                        total_pnl = pnl_summary.get("total_pnl_quote", 0)
                        total_pnl_pct = pnl_summary.get("total_pnl_pct", 0)
                        st.metric("Total PnL", f"${total_pnl:.6f}", delta=f"{total_pnl_pct:.2f}%")
                    with pnl_col2:
                        fees = pnl_summary.get("total_fees_value_quote", 0)
                        st.metric("Fees Earned", f"${fees:.6f}")
                    with pnl_col3:
                        il = pnl_summary.get("impermanent_loss_quote", 0)
                        st.metric("IL", f"${il:.6f}")
                    with pnl_col4:
                        apr = pnl_summary.get("fee_apr_estimate")
                        if apr:
                            st.metric("Fee APR", f"{apr:.1f}%")
                        else:
                            st.metric("Fee APR", "N/A")
            else:
                st.info("No CLMM positions found. Open a position to see range data.")
        except Exception as e:
            st.warning(f"Could not load position data: {e}")


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
