"""Monitor page - Detailed LP strategy monitoring.

专注于 LP 策略的详细监控：
- CLMM 仓位状态和可视化
- 实时价格追踪
- 手续费收益
- 无常损失
- PnL 历史
"""
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="📈", show_readme=False)

api = get_backend_api_client()

REFRESH_INTERVAL = 10

# Initialize session state
if "monitor_auto_refresh" not in st.session_state:
    st.session_state.monitor_auto_refresh = True
if "selected_monitor_bot" not in st.session_state:
    st.session_state.selected_monitor_bot = None


def get_running_bots():
    """Get list of running bots from MQTT."""
    try:
        status_response = api.get_all_bots_status()
        if status_response.get("status") == "success":
            bots = []
            for bot_name, bot_info in status_response.get("data", {}).items():
                # Only include running bots
                try:
                    bot_status = api.get_bot_status(bot_name)
                    if bot_status.get("status") == "success":
                        if bot_status.get("data", {}).get("status") == "running":
                            bots.append(bot_name)
                except Exception:
                    continue
            return sorted(bots)
    except Exception:
        pass
    return []


def create_price_range_gauge(current_price: float, lower_price: float, upper_price: float):
    """Create a gauge chart showing price position within range."""
    if upper_price <= lower_price:
        return None

    # Calculate position percentage
    range_width = upper_price - lower_price
    position_pct = (current_price - lower_price) / range_width * 100
    position_pct = max(0, min(100, position_pct))

    # Determine color based on position
    if 20 <= position_pct <= 80:
        bar_color = "#00D26A"  # Green - good position
    elif 10 <= position_pct <= 90:
        bar_color = "#FFA500"  # Orange - caution
    else:
        bar_color = "#FF4444"  # Red - near edge

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=position_pct,
        number={"suffix": "%", "font": {"size": 24}},
        delta={"reference": 50, "relative": False, "position": "bottom"},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": bar_color, "thickness": 0.75},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": [
                {"range": [0, 20], "color": "rgba(255, 68, 68, 0.3)"},
                {"range": [20, 80], "color": "rgba(0, 210, 106, 0.3)"},
                {"range": [80, 100], "color": "rgba(255, 68, 68, 0.3)"},
            ],
            "threshold": {
                "line": {"color": "white", "width": 2},
                "thickness": 0.75,
                "value": position_pct
            }
        },
        title={"text": "Price Position in Range", "font": {"size": 14}}
    ))

    fig.update_layout(
        height=200,
        margin=dict(l=20, r=20, t=40, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        font={"color": "white"}
    )

    return fig


# Page Header
st.title("📈 LP Strategy Monitor")
st.subheader("Detailed monitoring for your LP positions")

# Bot Selection
col1, col2, col3 = st.columns([3, 1, 1])

running_bots = get_running_bots()

with col1:
    if running_bots:
        # Preserve selection if still valid
        default_idx = 0
        if st.session_state.selected_monitor_bot in running_bots:
            default_idx = running_bots.index(st.session_state.selected_monitor_bot)

        selected_bot = st.selectbox(
            "Select Bot",
            options=running_bots,
            index=default_idx,
            label_visibility="collapsed",
        )
        st.session_state.selected_monitor_bot = selected_bot
    else:
        selected_bot = None
        st.warning("No running bots found")

with col2:
    refresh_label = "⏸️ Pause" if st.session_state.monitor_auto_refresh else "▶️ Auto"
    if st.button(refresh_label, use_container_width=True):
        st.session_state.monitor_auto_refresh = not st.session_state.monitor_auto_refresh
        st.rerun()

with col3:
    if st.button("🔄 Refresh", use_container_width=True, type="primary"):
        st.rerun()

if not selected_bot:
    st.info("Deploy and start a strategy to begin monitoring.")
    if st.button("🚀 Go to Deploy", type="primary"):
        st.switch_page("pages/2_Deploy.py")
    st.stop()


@st.fragment(run_every=REFRESH_INTERVAL if st.session_state.monitor_auto_refresh else None)
def monitor_content():
    """Main monitoring content with auto-refresh."""

    # Get bot status
    try:
        status_response = api.get_bot_status(selected_bot)
        bot_data = status_response.get("data", {})
    except Exception as e:
        st.error(f"Failed to get bot status: {e}")
        return

    status = bot_data.get("status", "unknown")
    performance = bot_data.get("performance", {})

    # Bot Status Header
    with st.container(border=True):
        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

        with col1:
            if status == "running":
                st.success(f"🤖 **{selected_bot}** - Running")
            else:
                st.warning(f"🤖 **{selected_bot}** - {status}")

        with col2:
            uptime = bot_data.get("uptime", "N/A")
            st.metric("⏱️ Uptime", uptime)

        with col3:
            if st.session_state.monitor_auto_refresh:
                st.metric("🔄 Refresh", f"{REFRESH_INTERVAL}s")
            else:
                st.metric("🔄 Refresh", "Paused")

        with col4:
            if status == "running":
                if st.button("⏹️ Stop", use_container_width=True, key="stop_monitor"):
                    with st.spinner("Stopping..."):
                        try:
                            api.stop_and_archive_bot(selected_bot)
                            st.success("Stopped")
                            time.sleep(2)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

    # Calculate totals from performance
    total_pnl = 0
    total_unrealized = 0
    total_volume = 0
    total_fees = 0

    for controller_id, inner_dict in performance.items():
        if isinstance(inner_dict, dict) and inner_dict.get("status") != "error":
            perf = inner_dict.get("performance", {})
            total_pnl += perf.get("global_pnl_quote", 0)
            total_unrealized += perf.get("unrealized_pnl_quote", 0)
            total_volume += perf.get("volume_traded", 0)
            total_fees += perf.get("fees_collected", 0)

    # Performance Overview
    with st.container(border=True):
        st.markdown("### 📊 Performance Overview")

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            pnl_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric(
                "🏦 NET PnL",
                f"${total_pnl:.4f}",
                delta=f"${total_unrealized:.4f} unrealized",
                delta_color=pnl_color,
            )

        with col2:
            st.metric("💰 Fees Earned", f"${total_fees:.6f}")

        with col3:
            pnl_pct = (total_pnl / total_volume * 100) if total_volume > 0 else 0
            st.metric("📈 PnL %", f"{pnl_pct:.4f}%")

        with col4:
            st.metric("💸 Volume", f"${total_volume:.2f}")

    # CLMM Position Section
    with st.container(border=True):
        st.markdown("### 🎯 CLMM Position Status")

        try:
            positions_response = api.get_clmm_positions(status="OPEN", limit=5)
            positions_data = positions_response.get("data", [])

            if positions_data:
                # Show all open positions
                for idx, position in enumerate(positions_data):
                    if idx > 0:
                        st.divider()

                    trading_pair = position.get("trading_pair", "Unknown")
                    connector = position.get("connector", "Unknown")
                    current_price = position.get("current_price", 0)
                    lower_price = position.get("lower_price", 0)
                    upper_price = position.get("upper_price", 0)
                    in_range = position.get("in_range", "UNKNOWN")
                    pnl_summary = position.get("pnl_summary", {})

                    # Position header
                    range_status = "🟢 IN RANGE" if in_range == "IN_RANGE" else "🔴 OUT OF RANGE"
                    st.markdown(f"**{trading_pair}** on {connector} | {range_status}")

                    # Price info and gauge
                    col1, col2 = st.columns([1, 1])

                    with col1:
                        price_col1, price_col2, price_col3 = st.columns(3)
                        with price_col1:
                            st.metric("Lower", f"{lower_price:.8f}")
                        with price_col2:
                            st.metric("Current", f"{current_price:.8f}")
                        with price_col3:
                            st.metric("Upper", f"{upper_price:.8f}")

                        # Progress bar
                        if upper_price > lower_price:
                            range_pct = (current_price - lower_price) / (upper_price - lower_price)
                            range_pct = max(0, min(1, range_pct))
                            st.progress(range_pct, text=f"Position: {range_pct:.1%} through range")

                    with col2:
                        gauge = create_price_range_gauge(current_price, lower_price, upper_price)
                        if gauge:
                            st.plotly_chart(gauge, use_container_width=True)

                    # PnL Summary
                    if pnl_summary:
                        pnl_col1, pnl_col2, pnl_col3, pnl_col4 = st.columns(4)
                        with pnl_col1:
                            pos_pnl = pnl_summary.get("total_pnl_quote", 0)
                            pos_pnl_pct = pnl_summary.get("total_pnl_pct", 0)
                            st.metric("Total PnL", f"${pos_pnl:.6f}", delta=f"{pos_pnl_pct:.2f}%")
                        with pnl_col2:
                            fees = pnl_summary.get("total_fees_value_quote", 0)
                            st.metric("Fees", f"${fees:.6f}")
                        with pnl_col3:
                            il = pnl_summary.get("impermanent_loss_quote", 0)
                            st.metric("IL", f"${il:.6f}")
                        with pnl_col4:
                            apr = pnl_summary.get("fee_apr_estimate")
                            st.metric("Fee APR", f"{apr:.1f}%" if apr else "N/A")
            else:
                st.info("No open CLMM positions found.")

        except Exception as e:
            st.warning(f"Could not load position data: {e}")

    # Controller Performance Details
    with st.container(border=True):
        st.markdown("### 🎛️ Controller Performance")

        if performance:
            for controller_id, inner_dict in performance.items():
                if isinstance(inner_dict, dict):
                    controller_status = inner_dict.get("status", "unknown")

                    if controller_status == "error":
                        st.error(f"**{controller_id}**: Error - {inner_dict.get('error', 'Unknown')}")
                    else:
                        perf = inner_dict.get("performance", {})

                        with st.expander(f"📊 {controller_id}", expanded=True):
                            col1, col2, col3, col4 = st.columns(4)

                            with col1:
                                realized = perf.get("realized_pnl_quote", 0)
                                st.metric("Realized PnL", f"${realized:.4f}")

                            with col2:
                                unrealized = perf.get("unrealized_pnl_quote", 0)
                                st.metric("Unrealized PnL", f"${unrealized:.4f}")

                            with col3:
                                net = perf.get("global_pnl_quote", 0)
                                st.metric("NET PnL", f"${net:.4f}")

                            with col4:
                                vol = perf.get("volume_traded", 0)
                                st.metric("Volume", f"${vol:.2f}")

                            # Close type counts
                            close_types = perf.get("close_type_counts", {})
                            if close_types:
                                st.caption("Close Types: " + " | ".join([
                                    f"TP:{close_types.get('CloseType.TAKE_PROFIT', 0)}",
                                    f"SL:{close_types.get('CloseType.STOP_LOSS', 0)}",
                                    f"TS:{close_types.get('CloseType.TRAILING_STOP', 0)}",
                                    f"TL:{close_types.get('CloseType.TIME_LIMIT', 0)}",
                                    f"ES:{close_types.get('CloseType.EARLY_STOP', 0)}",
                                ]))
        else:
            st.info("No controller performance data available")

    # Logs Section
    error_logs = bot_data.get("error_logs", [])
    general_logs = bot_data.get("general_logs", [])

    if error_logs or general_logs:
        with st.container(border=True):
            st.markdown("### 📋 Recent Logs")

            tab1, tab2 = st.tabs([f"📝 General ({len(general_logs)})", f"❌ Errors ({len(error_logs)})"])

            with tab1:
                if general_logs:
                    log_lines = []
                    for log in general_logs[:30]:
                        ts = log.get("timestamp", 0)
                        if isinstance(ts, (int, float)):
                            ts = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                        msg = log.get("msg", "")
                        log_lines.append(f"[{ts}] {msg}")
                    st.code("\n".join(log_lines), language="log")
                else:
                    st.info("No general logs")

            with tab2:
                if error_logs:
                    log_lines = []
                    for log in error_logs[:30]:
                        ts = log.get("timestamp", "")
                        if isinstance(ts, (int, float)):
                            ts = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                        msg = log.get("msg", "")
                        log_lines.append(f"[{ts}] {msg}")
                    st.code("\n".join(log_lines), language="log")
                else:
                    st.info("No error logs")


# Run the monitoring content
monitor_content()

# Auto-refresh indicator
if st.session_state.monitor_auto_refresh:
    st.caption(f"🔄 Auto-refreshing every {REFRESH_INTERVAL} seconds")
