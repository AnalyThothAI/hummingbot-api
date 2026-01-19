"""Overview page - Strategy instances and management."""
import time

import pandas as pd
import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="🦅", show_readme=False)

# Initialize backend client
api = get_backend_api_client()

# Initialize session state for auto-refresh
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = True

REFRESH_INTERVAL = 10  # seconds


def stop_bot(bot_name: str):
    """Stop a running bot."""
    try:
        api.stop_and_archive_bot(bot_name)
        st.success(f"Bot {bot_name} stopped and archived successfully")
        time.sleep(2)
    except Exception as e:
        st.error(f"Failed to stop bot {bot_name}: {e}")


def archive_bot(bot_name: str):
    """Archive a stopped bot."""
    try:
        api.stop_container(bot_name)
        api.remove_container(bot_name)
        st.success(f"Bot {bot_name} archived successfully")
        time.sleep(1)
    except Exception as e:
        st.error(f"Failed to archive bot {bot_name}: {e}")


def render_bot_card(bot_name: str):
    """Render a bot performance card."""
    try:
        bot_status = api.get_bot_status(bot_name)

        with st.container(border=True):
            if bot_status.get("status") == "error":
                st.error(f"🤖 **{bot_name}** - Not Available")
                st.caption("Error fetching bot status. Please check the bot client.")
            else:
                bot_data = bot_status.get("data", {})
                is_running = bot_data.get("status") == "running"
                performance = bot_data.get("performance", {})
                error_logs = bot_data.get("error_logs", [])
                general_logs = bot_data.get("general_logs", [])

                # Bot header
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if is_running:
                        st.success(f"🤖 **{bot_name}** - Running")
                    else:
                        st.warning(f"🤖 **{bot_name}** - Stopped")

                with col3:
                    if is_running:
                        if st.button("⏹️ Stop", key=f"stop_{bot_name}", use_container_width=True):
                            stop_bot(bot_name)
                    else:
                        if st.button("📦 Archive", key=f"archive_{bot_name}", use_container_width=True):
                            archive_bot(bot_name)

                if is_running and performance:
                    # Calculate totals from performance data
                    total_pnl = 0
                    total_unrealized = 0
                    total_volume = 0

                    active_controllers = []
                    stopped_controllers = []
                    error_controllers = []

                    for controller, inner_dict in performance.items():
                        if inner_dict.get("status") == "error":
                            error_controllers.append({
                                "Controller": controller,
                                "Error": inner_dict.get("error", "Unknown error")
                            })
                            continue

                        perf = inner_dict.get("performance", {})
                        realized_pnl = perf.get("realized_pnl_quote", 0)
                        unrealized_pnl = perf.get("unrealized_pnl_quote", 0)
                        global_pnl = perf.get("global_pnl_quote", 0)
                        volume = perf.get("volume_traded", 0)

                        total_pnl += global_pnl
                        total_unrealized += unrealized_pnl
                        total_volume += volume

                        close_types = perf.get("close_type_counts", {})
                        tp = close_types.get("CloseType.TAKE_PROFIT", 0)
                        sl = close_types.get("CloseType.STOP_LOSS", 0)
                        ts = close_types.get("CloseType.TRAILING_STOP", 0)

                        controller_info = {
                            "Controller": controller,
                            "Realized PnL": f"${realized_pnl:.2f}",
                            "Unrealized PnL": f"${unrealized_pnl:.2f}",
                            "NET PnL": f"${global_pnl:.2f}",
                            "Volume": f"${volume:.2f}",
                            "TP/SL/TS": f"{tp}/{sl}/{ts}",
                        }
                        active_controllers.append(controller_info)

                    total_pnl_pct = total_pnl / total_volume if total_volume > 0 else 0

                    # Display metrics
                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        st.metric("🏦 NET PnL", f"${total_pnl:.2f}")
                    with col2:
                        st.metric("💹 Unrealized PnL", f"${total_unrealized:.2f}")
                    with col3:
                        st.metric("📊 NET PnL (%)", f"{total_pnl_pct:.2%}")
                    with col4:
                        st.metric("💸 Volume Traded", f"${total_volume:.2f}")

                    # Active controllers table
                    if active_controllers:
                        st.success(f"🚀 **Active Controllers:** {len(active_controllers)} controller(s) running")
                        st.dataframe(
                            pd.DataFrame(active_controllers),
                            use_container_width=True,
                            hide_index=True,
                        )

                    # Error controllers
                    if error_controllers:
                        st.error(f"💀 **Controllers with Errors:** {len(error_controllers)} controller(s)")
                        st.dataframe(
                            pd.DataFrame(error_controllers),
                            use_container_width=True,
                            hide_index=True,
                        )

                # Logs sections
                with st.expander("📋 Error Logs"):
                    if error_logs:
                        for log in error_logs[:20]:
                            timestamp = log.get("timestamp", "")
                            message = log.get("msg", "")
                            st.text(f"{timestamp} - {message}")
                    else:
                        st.info("No error logs available.")

                with st.expander("📝 General Logs"):
                    if general_logs:
                        for log in general_logs[:20]:
                            timestamp = log.get("timestamp", "")
                            message = log.get("msg", "")
                            st.text(f"{timestamp} - {message}")
                    else:
                        st.info("No general logs available.")

    except Exception as e:
        with st.container(border=True):
            st.error(f"🤖 **{bot_name}** - Error: {str(e)}")


# Page Header
st.title("🦅 Strategy Overview")
st.subheader("Monitor and manage your active LP strategy instances")

# Auto-refresh controls
with st.container(border=True):
    st.info("🔄 **Auto-refresh Controls:** Configure automatic data refresh")

    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        if st.session_state.auto_refresh_enabled:
            st.success(f"Auto-refreshing every {REFRESH_INTERVAL} seconds")
        else:
            st.warning("Auto-refresh paused")

    with col2:
        refresh_label = "⏸️ Pause" if st.session_state.auto_refresh_enabled else "▶️ Resume"
        if st.button(refresh_label, use_container_width=True):
            st.session_state.auto_refresh_enabled = not st.session_state.auto_refresh_enabled
            st.rerun()

    with col3:
        if st.button("🔄 Refresh Now", use_container_width=True, type="primary"):
            st.rerun()


@st.fragment(run_every=REFRESH_INTERVAL if st.session_state.auto_refresh_enabled else None)
def show_bot_instances():
    """Fragment to display bot instances with auto-refresh."""
    # Active Bots Section
    with st.container(border=True):
        st.success("🤖 **Active Bot Instances:** Strategy bots currently deployed")

        try:
            # Try to get active bots from bot orchestration
            try:
                active_bots_response = api.get_all_bots_status()
                if active_bots_response.get("status") == "success":
                    active_bots = active_bots_response.get("data", {})
                else:
                    active_bots = {}
            except Exception:
                active_bots = {}

            # Also get from Docker containers
            try:
                containers = api.get_active_containers(name_filter="hummingbot")
                container_names = [c.get("name", "") for c in containers if c.get("name")]
            except Exception:
                container_names = []

            # Merge bot names
            all_bots = set(active_bots.keys()) | set(container_names)

            if all_bots:
                for bot_name in sorted(all_bots):
                    render_bot_card(bot_name)
            else:
                st.warning("⚠️ No active bot instances found. Deploy a bot to see it here.")

                if st.button("🚀 Deploy New Strategy", type="primary"):
                    st.switch_page("pages/2_Deploy.py")

        except Exception as e:
            st.error(f"Failed to connect to backend: {e}")
            st.info("Please make sure the backend is running and accessible.")


# Call the fragment
show_bot_instances()
