"""Overview page - Strategy instances management.

核心设计理念（来自官方 hummingbot-dashboard）：
1. 实时状态来自 MQTT（get_active_bots_status），这是唯一真实来源
2. 不查询数据库 bot_runs - 那是历史归档页面的工作
3. Controller 级别管理：可以单独启停每个 controller
4. 在实例卡片上直接显示 logs
"""
import time
from datetime import datetime
from typing import Dict, List, Any

import pandas as pd
import streamlit as st

from st_utils import (
    initialize_st_page,
    get_backend_api_client,
    clear_all_caches,
)

initialize_st_page(icon="🦅", show_readme=False)

api = get_backend_api_client()
REFRESH_INTERVAL = 10  # seconds

# Initialize session state
if "overview_auto_refresh" not in st.session_state:
    st.session_state.overview_auto_refresh = True


def stop_and_archive_bot(bot_name: str):
    """Stop a running bot and archive it."""
    try:
        result = api.stop_and_archive_bot(bot_name)
        if result.get("status") == "success":
            st.success(f"Bot {bot_name} stopped and archived successfully")
            time.sleep(2)
        else:
            st.error(f"Failed to stop bot: {result.get('message', 'Unknown error')}")
    except Exception as e:
        st.error(f"Failed to stop bot {bot_name}: {e}")


def archive_bot(bot_name: str):
    """Archive a stopped bot (remove container)."""
    try:
        api.stop_container(bot_name)
        api.remove_container(bot_name)
        st.success(f"Bot {bot_name} archived successfully")
        time.sleep(1)
    except Exception as e:
        st.error(f"Failed to archive bot {bot_name}: {e}")


def stop_controllers(bot_name: str, controllers: List[str]):
    """Stop selected controllers by setting manual_kill_switch=True."""
    success_count = 0
    for controller in controllers:
        try:
            api.update_bot_controller_config(
                bot_name,
                controller,
                {"manual_kill_switch": True}
            )
            success_count += 1
        except Exception as e:
            st.error(f"Failed to stop controller {controller}: {e}")

    if success_count > 0:
        st.success(f"Successfully stopped {success_count} controller(s)")
        # Temporarily disable auto-refresh to show message
        st.session_state.overview_auto_refresh = False
    return success_count > 0


def start_controllers(bot_name: str, controllers: List[str]):
    """Start selected controllers by setting manual_kill_switch=False."""
    success_count = 0
    for controller in controllers:
        try:
            api.update_bot_controller_config(
                bot_name,
                controller,
                {"manual_kill_switch": False}
            )
            success_count += 1
        except Exception as e:
            st.error(f"Failed to start controller {controller}: {e}")

    if success_count > 0:
        st.success(f"Successfully started {success_count} controller(s)")
        st.session_state.overview_auto_refresh = False
    return success_count > 0


def render_bot_card(bot_name: str):
    """Render a bot performance card following official dashboard pattern."""
    try:
        # Get bot status from MQTT (the only true source)
        bot_status = api.get_bot_status(bot_name)

        # Get controller configs if bot is running
        controller_configs = []
        if bot_status.get("status") == "success":
            bot_data = bot_status.get("data", {})
            is_running = bot_data.get("status") == "running"
            if is_running:
                try:
                    controller_configs = api.get_bot_controller_configs(bot_name)
                    controller_configs = controller_configs if controller_configs else []
                except Exception:
                    controller_configs = []

        with st.container(border=True):
            if bot_status.get("status") == "error":
                # Error state
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.error(f"🤖 **{bot_name}** - Not Available")
                st.error(f"Error fetching bot status. Check the bot client.")
            else:
                bot_data = bot_status.get("data", {})
                is_running = bot_data.get("status") == "running"
                performance = bot_data.get("performance", {})
                error_logs = bot_data.get("error_logs", [])
                general_logs = bot_data.get("general_logs", [])

                # Bot header
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    if is_running:
                        st.success(f"🤖 **{bot_name}** - Running")
                    else:
                        st.warning(f"🤖 **{bot_name}** - Stopped")

                with col3:
                    if is_running:
                        if st.button("⏹️ Stop", key=f"stop_{bot_name}", use_container_width=True):
                            stop_and_archive_bot(bot_name)
                            st.rerun()
                    else:
                        if st.button("📦 Archive", key=f"archive_{bot_name}", use_container_width=True):
                            archive_bot(bot_name)
                            st.rerun()

                if is_running:
                    # Parse performance data and categorize controllers
                    active_controllers = []
                    stopped_controllers = []
                    error_controllers = []
                    total_global_pnl_quote = 0
                    total_volume_traded = 0
                    total_unrealized_pnl_quote = 0

                    for controller_id, inner_dict in performance.items():
                        controller_status = inner_dict.get("status")
                        if controller_status == "error":
                            error_controllers.append({
                                "Controller": controller_id,
                                "Error": inner_dict.get("error", "Unknown error")
                            })
                            continue

                        controller_performance = inner_dict.get("performance", {})

                        # Find matching controller config
                        controller_config = next(
                            (config for config in controller_configs
                             if config.get("id") == controller_id or config.get("_config_name") == controller_id),
                            {}
                        )

                        controller_name = controller_config.get("controller_name", controller_id)
                        connector_name = controller_config.get("connector_name", "N/A")
                        trading_pair = controller_config.get("trading_pair", "N/A")
                        kill_switch_status = controller_config.get("manual_kill_switch", False)

                        realized_pnl_quote = controller_performance.get("realized_pnl_quote", 0)
                        unrealized_pnl_quote = controller_performance.get("unrealized_pnl_quote", 0)
                        global_pnl_quote = controller_performance.get("global_pnl_quote", 0)
                        volume_traded = controller_performance.get("volume_traded", 0)

                        close_types = controller_performance.get("close_type_counts", {})
                        tp = close_types.get("CloseType.TAKE_PROFIT", 0)
                        sl = close_types.get("CloseType.STOP_LOSS", 0)
                        time_limit = close_types.get("CloseType.TIME_LIMIT", 0)
                        ts = close_types.get("CloseType.TRAILING_STOP", 0)
                        refreshed = close_types.get("CloseType.EARLY_STOP", 0)
                        failed = close_types.get("CloseType.FAILED", 0)
                        close_types_str = f"TP:{tp} SL:{sl} TS:{ts} TL:{time_limit} ES:{refreshed} F:{failed}"

                        controller_info = {
                            "Select": False,
                            "ID": controller_id,
                            "Controller": controller_name,
                            "Connector": connector_name,
                            "Trading Pair": trading_pair,
                            "Realized PnL ($)": round(realized_pnl_quote, 2),
                            "Unrealized PnL ($)": round(unrealized_pnl_quote, 2),
                            "NET PnL ($)": round(global_pnl_quote, 2),
                            "Volume ($)": round(volume_traded, 2),
                            "Close Types": close_types_str,
                            "_controller_id": controller_id
                        }

                        if kill_switch_status:
                            stopped_controllers.append(controller_info)
                        else:
                            active_controllers.append(controller_info)

                        total_global_pnl_quote += global_pnl_quote
                        total_volume_traded += volume_traded
                        total_unrealized_pnl_quote += unrealized_pnl_quote

                    total_global_pnl_pct = (total_global_pnl_quote / total_volume_traded * 100) if total_volume_traded > 0 else 0

                    # Display metrics
                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        st.metric("🏦 NET PnL", f"${total_global_pnl_quote:.2f}")
                    with col2:
                        st.metric("💹 Unrealized PnL", f"${total_unrealized_pnl_quote:.2f}")
                    with col3:
                        st.metric("📊 NET PnL (%)", f"{total_global_pnl_pct:.2f}%")
                    with col4:
                        st.metric("💸 Volume Traded", f"${total_volume_traded:.2f}")

                    # Active Controllers
                    if active_controllers:
                        st.success("🚀 **Active Controllers:** Controllers currently running and trading")
                        active_df = pd.DataFrame(active_controllers)

                        edited_active_df = st.data_editor(
                            active_df,
                            column_config={
                                "Select": st.column_config.CheckboxColumn(
                                    "Select",
                                    help="Select controllers to stop",
                                    default=False,
                                ),
                                "_controller_id": None,  # Hide this column
                            },
                            disabled=[col for col in active_df.columns if col != "Select"],
                            hide_index=True,
                            use_container_width=True,
                            key=f"active_table_{bot_name}"
                        )

                        selected_active = [
                            row["_controller_id"]
                            for _, row in edited_active_df.iterrows()
                            if row["Select"]
                        ]

                        if selected_active:
                            if st.button(f"⏹️ Stop Selected ({len(selected_active)})",
                                         key=f"stop_active_{bot_name}",
                                         type="secondary"):
                                with st.spinner(f"Stopping {len(selected_active)} controller(s)..."):
                                    stop_controllers(bot_name, selected_active)
                                    time.sleep(1)
                                    st.rerun()

                    # Stopped Controllers
                    if stopped_controllers:
                        st.warning("💤 **Stopped Controllers:** Controllers that are paused or stopped")
                        stopped_df = pd.DataFrame(stopped_controllers)

                        edited_stopped_df = st.data_editor(
                            stopped_df,
                            column_config={
                                "Select": st.column_config.CheckboxColumn(
                                    "Select",
                                    help="Select controllers to start",
                                    default=False,
                                ),
                                "_controller_id": None,  # Hide this column
                            },
                            disabled=[col for col in stopped_df.columns if col != "Select"],
                            hide_index=True,
                            use_container_width=True,
                            key=f"stopped_table_{bot_name}"
                        )

                        selected_stopped = [
                            row["_controller_id"]
                            for _, row in edited_stopped_df.iterrows()
                            if row["Select"]
                        ]

                        if selected_stopped:
                            if st.button(f"▶️ Start Selected ({len(selected_stopped)})",
                                         key=f"start_stopped_{bot_name}",
                                         type="primary"):
                                with st.spinner(f"Starting {len(selected_stopped)} controller(s)..."):
                                    start_controllers(bot_name, selected_stopped)
                                    time.sleep(1)
                                    st.rerun()

                    # Error Controllers
                    if error_controllers:
                        st.error("💀 **Controllers with Errors:** Controllers that encountered errors")
                        error_df = pd.DataFrame(error_controllers)
                        st.dataframe(error_df, use_container_width=True, hide_index=True)

                # Logs sections (available for both running and stopped bots)
                with st.expander("📋 Error Logs"):
                    if error_logs:
                        for log in error_logs[:50]:
                            timestamp = log.get("timestamp", "")
                            if isinstance(timestamp, (int, float)):
                                timestamp = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                            message = log.get("msg", "")
                            logger_name = log.get("logger_name", "")
                            st.text(f"{timestamp} - {logger_name}: {message}")
                    else:
                        st.info("No error logs available.")

                with st.expander("📝 General Logs"):
                    if general_logs:
                        for log in general_logs[:50]:
                            timestamp = log.get("timestamp", 0)
                            if isinstance(timestamp, (int, float)):
                                timestamp = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                            message = log.get("msg", "")
                            logger_name = log.get("logger_name", "")
                            st.text(f"{timestamp} - {logger_name}: {message}")
                    else:
                        st.info("No general logs available.")

    except Exception as e:
        with st.container(border=True):
            st.error(f"🤖 **{bot_name}** - Error")
            st.error(f"An error occurred while fetching bot status: {str(e)}")


# Page Header
st.title("🦅 Strategy Instances")
st.subheader("Monitor and manage your LP strategy instances")

# Auto-refresh controls
col1, col2, col3 = st.columns([3, 1, 1])

# Create placeholder for status message
status_placeholder = col1.empty()

with col2:
    refresh_label = "▶️ Start Auto-refresh" if not st.session_state.overview_auto_refresh else "⏸️ Stop Auto-refresh"
    if st.button(refresh_label, use_container_width=True):
        st.session_state.overview_auto_refresh = not st.session_state.overview_auto_refresh
        st.rerun()

with col3:
    if st.button("🔄 Refresh Now", use_container_width=True, type="primary"):
        # Re-enable auto-refresh if it was temporarily disabled
        if not st.session_state.overview_auto_refresh:
            st.session_state.overview_auto_refresh = True
        clear_all_caches()
        st.rerun()


@st.fragment(run_every=REFRESH_INTERVAL if st.session_state.overview_auto_refresh else None)
def show_bot_instances():
    """Fragment to display bot instances with auto-refresh.

    Uses ONLY MQTT real-time data - no database queries.
    """
    try:
        # Get all active bots from MQTT (the only true source)
        active_bots_response = api.get_all_bots_status()

        if active_bots_response.get("status") == "success":
            active_bots = active_bots_response.get("data", {})

            # Filter to only show bots with meaningful status
            truly_active_bots = {}
            for bot_name, bot_info in active_bots.items():
                try:
                    bot_status = api.get_bot_status(bot_name)
                    if bot_status.get("status") == "success":
                        bot_data = bot_status.get("data", {})
                        if bot_data.get("status") in ["running", "stopped"]:
                            truly_active_bots[bot_name] = bot_info
                except Exception:
                    continue

            if truly_active_bots:
                # Show refresh status
                if st.session_state.overview_auto_refresh:
                    status_placeholder.info(f"🔄 Auto-refreshing every {REFRESH_INTERVAL} seconds")
                else:
                    status_placeholder.warning("⏸️ Auto-refresh paused. Click 'Refresh Now' to resume.")

                # Render each bot
                for bot_name in truly_active_bots.keys():
                    render_bot_card(bot_name)
            else:
                status_placeholder.info("No active bot instances found. Deploy a bot to see it here.")
                with st.container(border=True):
                    st.info("📭 **No active bot instances.**")
                    st.markdown("Deploy a strategy to get started.")
                    if st.button("🚀 Deploy New Strategy", type="primary"):
                        st.switch_page("pages/2_Deploy.py")
        else:
            st.error("Failed to fetch active bots status.")

    except Exception as e:
        st.error(f"Failed to connect to backend: {e}")
        st.info("Please make sure the backend is running and accessible.")


# Call the fragment
show_bot_instances()
