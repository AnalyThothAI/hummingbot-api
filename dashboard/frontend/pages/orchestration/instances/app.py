from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from frontend.st_utils import backend_api_request, get_backend_api_client, initialize_st_page

initialize_st_page(icon="🦅", show_readme=False)

# Initialize backend client
backend_api_client = get_backend_api_client()

REFRESH_INTERVAL = 10

STATUS_REASON_MAP = {
    "mqtt_disconnected": "MQTT disconnected",
    "mqtt_stale": "No recent MQTT signal",
    "container_stopped": "Container stopped",
    "container_missing": "Container not found",
}

HEALTH_LABELS = {
    "running": "🟢 Running",
    "degraded": "🟠 Degraded",
    "stopped": "🔴 Stopped",
    "orphaned": "🟣 Orphaned",
    "unknown": "⚪ Unknown",
}

DOCKER_LABELS = {
    "running": "Running",
    "exited": "Stopped",
    "created": "Created",
    "dead": "Dead",
    "missing": "Missing",
    "unknown": "Unknown",
}

MQTT_LABELS = {
    "connected": "Connected",
    "stale": "Stale",
    "disconnected": "Disconnected",
    "unknown": "Unknown",
}

STRATEGY_LABELS = {
    "running": "Running",
    "idle": "Idle",
    "stopped": "Stopped",
    "stopping": "Stopping",
    "unknown": "Unknown",
}


def format_label(value: Optional[str], mapping: Dict[str, str]) -> str:
    if not value:
        return mapping.get("unknown", "Unknown")
    return mapping.get(value, value.replace("_", " ").title())


def format_number(value: Optional[float], precision: int = 2) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:,.{precision}f}"


def format_age(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        total_seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return None
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def response_ok(response: Dict[str, Any]) -> bool:
    if not response.get("ok"):
        return False
    data = response.get("data", {})
    if isinstance(data, dict) and data.get("status") in {"error", "failed"}:
        return False
    return True


def response_has_success_flag(response: Dict[str, Any]) -> bool:
    data = response.get("data", {})
    if not isinstance(data, dict):
        return True
    inner = data.get("response")
    if not isinstance(inner, dict):
        return True
    return inner.get("success", True) is True


def handle_action_response(
    response: Dict[str, Any],
    success_message: str,
    error_message: str,
    require_success_flag: bool = False,
) -> bool:
    if response_ok(response):
        if require_success_flag and not response_has_success_flag(response):
            data = response.get("data", {})
            message = error_message
            if isinstance(data, dict):
                inner = data.get("response", {})
                if isinstance(inner, dict):
                    message = inner.get("message") or error_message
            st.error(message)
            return False
        st.session_state.last_action_message = success_message
        st.session_state.last_action_level = "success"
        st.rerun()
        return True

    status_code = response.get("status_code")
    if status_code == 401:
        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
    else:
        data = response.get("data", {}) if isinstance(response.get("data"), dict) else {}
        message = data.get("message") or response.get("error") or error_message
        st.error(message)
    return False


def stop_bot(bot_name: str, skip_order_cancellation: bool = False):
    payload = {
        "bot_name": bot_name,
        "skip_order_cancellation": skip_order_cancellation,
    }
    response = backend_api_request("POST", "/bot-orchestration/stop-bot", json_body=payload)
    if handle_action_response(
        response,
        f"Stop strategy command sent to {bot_name}.",
        f"Failed to stop strategy for {bot_name}.",
        require_success_flag=True,
    ):
        return


def start_bot(bot_name: str):
    payload = {"bot_name": bot_name}
    response = backend_api_request("POST", "/bot-orchestration/start-bot", json_body=payload)
    if handle_action_response(
        response,
        f"Start strategy command sent to {bot_name}.",
        f"Failed to start strategy for {bot_name}.",
        require_success_flag=True,
    ):
        return


def stop_container(bot_name: str):
    response = backend_api_request("POST", f"/docker/stop-container/{bot_name}")
    if handle_action_response(response, f"Container {bot_name} stopping.", f"Failed to stop container {bot_name}."):
        return


def start_container(bot_name: str):
    response = backend_api_request("POST", f"/docker/start-container/{bot_name}")
    if handle_action_response(response, f"Container {bot_name} starting.", f"Failed to start container {bot_name}."):
        return


def archive_bot(bot_name: str, docker_status: str):
    if docker_status == "running":
        response = backend_api_request("POST", f"/bot-orchestration/stop-and-archive-bot/{bot_name}")
        success_message = f"Stop-and-archive initiated for {bot_name}."
        error_message = f"Failed to archive bot {bot_name}."
    else:
        response = backend_api_request("POST", f"/docker/remove-container/{bot_name}")
        success_message = f"Bot {bot_name} archived successfully."
        error_message = f"Failed to archive bot {bot_name}."

    if handle_action_response(response, success_message, error_message):
        return


def stop_controllers(bot_name: str, controllers: List[str]) -> bool:
    success_count = 0
    for controller in controllers:
        try:
            backend_api_client.controllers.update_bot_controller_config(
                bot_name,
                controller,
                {"manual_kill_switch": True}
            )
            success_count += 1
        except Exception as e:
            st.error(f"Failed to stop controller {controller}: {e}")

    if success_count > 0:
        st.session_state.last_action_message = f"Successfully stopped {success_count} controller(s)"
        st.session_state.last_action_level = "success"
        st.rerun()

    return success_count > 0


def start_controllers(bot_name: str, controllers: List[str]) -> bool:
    success_count = 0
    for controller in controllers:
        try:
            backend_api_client.controllers.update_bot_controller_config(
                bot_name,
                controller,
                {"manual_kill_switch": False}
            )
            success_count += 1
        except Exception as e:
            st.error(f"Failed to start controller {controller}: {e}")

    if success_count > 0:
        st.session_state.last_action_message = f"Successfully started {success_count} controller(s)"
        st.session_state.last_action_level = "success"
        st.rerun()

    return success_count > 0


def build_controller_signals(custom_info: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(custom_info, dict):
        return {"signals": "", "notes": ""}

    signals = []
    notes = []

    state = custom_info.get("state") or custom_info.get("controller_state") or custom_info.get("hedge_state")
    if state:
        signals.append(f"State: {state}")

    intent = custom_info.get("intent")
    if isinstance(intent, dict):
        flow = intent.get("flow")
        stage = intent.get("stage")
        reason = intent.get("reason")
        if flow or stage:
            intent_label = "/".join([value for value in [flow, stage] if value])
            signals.append(f"Intent: {intent_label}")
        if reason:
            notes.append(str(reason))

    positions = custom_info.get("positions")
    if isinstance(positions, dict):
        lp_active = positions.get("lp_active")
        swap_active = positions.get("swap_active")
        if lp_active is not None:
            signals.append(f"LP: {lp_active}")
        if swap_active is not None:
            signals.append(f"Swaps: {swap_active}")

    price = custom_info.get("price")
    if price is not None:
        signals.append(f"Price: {format_number(price, 4)}")

    wallet = custom_info.get("wallet")
    if isinstance(wallet, dict):
        base = wallet.get("base")
        quote = wallet.get("quote")
        if base is not None or quote is not None:
            base_str = format_number(base, 4) if base is not None else "-"
            quote_str = format_number(quote, 4) if quote is not None else "-"
            signals.append(f"Wallet: {base_str}/{quote_str}")

    stop_loss_active = False
    rebalance_pending = None
    flags = custom_info.get("flags")
    if isinstance(flags, dict):
        stop_loss_active = bool(flags.get("stop_loss_active"))
        rebalance_pending = flags.get("rebalance_pending")

    if custom_info.get("stop_loss_active") is not None:
        stop_loss_active = bool(custom_info.get("stop_loss_active"))
    if custom_info.get("rebalance_pending") is not None:
        rebalance_pending = custom_info.get("rebalance_pending")

    if stop_loss_active:
        signals.append("StopLoss: on")
    if rebalance_pending:
        signals.append(f"Rebalance: {rebalance_pending}")

    state_reason = custom_info.get("state_reason") or custom_info.get("intent_reason")
    if state_reason:
        notes.append(state_reason)

    return {
        "signals": " | ".join(signals),
        "notes": " | ".join(notes),
    }


def build_controller_rows(performance: Dict[str, Any], controller_configs: List[Dict[str, Any]]):
    active_controllers = []
    stopped_controllers = []
    error_controllers = []

    config_map = {}
    for config in controller_configs:
        if not isinstance(config, dict):
            continue
        config_id = config.get("id")
        if config_id:
            config_map[config_id] = config

    total_global_pnl_quote = 0
    total_volume_traded = 0
    total_unrealized_pnl_quote = 0

    if not isinstance(performance, dict):
        return active_controllers, stopped_controllers, error_controllers, total_global_pnl_quote, total_volume_traded, total_unrealized_pnl_quote, config_map

    for controller, inner_dict in performance.items():
        controller_status = inner_dict.get("status")
        if controller_status == "error":
            error_controllers.append({
                "Controller": controller,
                "Error": inner_dict.get("error", "Unknown error")
            })
            continue

        controller_performance = inner_dict.get("performance", {})
        custom_info = inner_dict.get("custom_info", {})
        controller_config = config_map.get(controller, {})

        controller_name = controller_config.get("controller_name", controller)
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
        close_types_str = f"TP: {tp} | SL: {sl} | TS: {ts} | TL: {time_limit} | ES: {refreshed} | F: {failed}"

        signals = build_controller_signals(custom_info)

        controller_info = {
            "Select": False,
            "ID": controller_config.get("id"),
            "Controller": controller_name,
            "Connector": connector_name,
            "Trading Pair": trading_pair,
            "Signals": signals.get("signals", ""),
            "Notes": signals.get("notes", ""),
            "Realized PNL ($)": round(realized_pnl_quote, 2),
            "Unrealized PNL ($)": round(unrealized_pnl_quote, 2),
            "NET PNL ($)": round(global_pnl_quote, 2),
            "Volume ($)": round(volume_traded, 2),
            "Close Types": close_types_str,
            "_controller_id": controller,
        }

        total_global_pnl_quote += global_pnl_quote
        total_volume_traded += volume_traded
        total_unrealized_pnl_quote += unrealized_pnl_quote

        if kill_switch_status:
            stopped_controllers.append(controller_info)
        else:
            active_controllers.append(controller_info)

    return active_controllers, stopped_controllers, error_controllers, total_global_pnl_quote, total_volume_traded, total_unrealized_pnl_quote, config_map


def build_lp_position_rows(performance: Dict[str, Any], config_map: Dict[str, Any]) -> List[Dict[str, str]]:
    rows = []
    if not isinstance(performance, dict):
        return rows

    for controller_id, inner_dict in performance.items():
        if not isinstance(inner_dict, dict):
            continue
        custom_info = inner_dict.get("custom_info", {})
        positions = custom_info.get("lp_positions")
        if not isinstance(positions, list) or not positions:
            continue

        controller_config = config_map.get(controller_id, {})
        controller_name = controller_config.get("controller_name", controller_id)
        trading_pair = controller_config.get("trading_pair")

        for pos in positions:
            if not isinstance(pos, dict):
                continue
            rows.append({
                "Controller": controller_name,
                "Pair": trading_pair or "-",
                "State": pos.get("state") or "-",
                "Position": pos.get("position") or "-",
                "Lower": format_number(pos.get("lower"), 6),
                "Upper": format_number(pos.get("upper"), 6),
                "Base": format_number(pos.get("base"), 4),
                "Quote": format_number(pos.get("quote"), 4),
            })

    return rows


def format_error_logs(error_logs: List[Any]) -> List[str]:
    formatted = []
    for log in error_logs:
        if isinstance(log, dict):
            timestamp = log.get("timestamp", "")
            message = log.get("msg", "")
            logger_name = log.get("logger_name", "")
            formatted.append(f"{timestamp} - {logger_name}: {message}".strip())
        else:
            formatted.append(str(log))
    return formatted


def format_general_logs(general_logs: List[Any]) -> List[str]:
    formatted = []
    for log in general_logs:
        if isinstance(log, dict):
            timestamp = log.get("timestamp", "")
            try:
                timestamp = pd.to_datetime(int(timestamp), unit="s")
            except Exception:
                pass
            message = log.get("msg", "")
            logger_name = log.get("logger_name", "")
            formatted.append(f"{timestamp} - {logger_name}: {message}".strip())
        else:
            formatted.append(str(log))
    return formatted


def filter_logs(lines: List[str], search: str, max_lines: int) -> List[str]:
    if search:
        lowered = search.lower()
        lines = [line for line in lines if lowered in line.lower()]
    if max_lines > 0:
        return lines[-max_lines:]
    return lines


def render_overview(instances: List[Dict[str, Any]]):
    filter_cols = st.columns([2, 1, 1])
    with filter_cols[0]:
        name_filter = st.text_input("Search instances", placeholder="Filter by name")
    with filter_cols[1]:
        status_filter = st.selectbox(
            "Health",
            options=["All", "Running", "Degraded", "Stopped", "Orphaned", "Unknown"],
            index=0,
        )

    filtered = instances
    if name_filter:
        name_filter_lower = name_filter.lower()
        filtered = [row for row in filtered if name_filter_lower in row.get("name", "").lower()]

    if status_filter != "All":
        target_state = status_filter.lower()
        filtered = [row for row in filtered if row.get("health_state") == target_state]

    if not filtered:
        st.info("No instances match the current filters.")
        return

    for instance in filtered:
        bot_name = instance.get("name", "Unknown")
        docker_status = instance.get("docker_status", "unknown")
        mqtt_status = instance.get("mqtt_status", "unknown")
        health_state = instance.get("health_state", "unknown")
        reason = instance.get("reason")
        last_seen_label = format_age(instance.get("mqtt_last_seen_age"))

        with st.container(border=True):
            header_cols = st.columns([4, 1])
            with header_cols[0]:
                st.markdown(f"**{bot_name}** · {format_label(health_state, HEALTH_LABELS)}")
                meta_parts = [
                    f"Docker: {format_label(docker_status, DOCKER_LABELS)}",
                    f"MQTT: {format_label(mqtt_status, MQTT_LABELS)}",
                ]
                if last_seen_label:
                    meta_parts.append(f"Last seen {last_seen_label} ago")
                st.caption(" • ".join(meta_parts))
                if reason:
                    st.caption(f"Reason: {STATUS_REASON_MAP.get(reason, reason)}")
            with header_cols[1]:
                show_details = st.toggle(
                    "Details",
                    value=True,
                    key=f"details_{bot_name}",
                )

            bot_status = {}
            bot_data = {}
            bot_status_value = None
            if show_details:
                try:
                    bot_status = backend_api_client.bot_orchestration.get_bot_status(bot_name)
                except Exception as exc:
                    bot_status = {"status": "error", "error": str(exc)}

                if bot_status.get("status") == "success":
                    bot_data = bot_status.get("data", {})
                    bot_status_value = bot_data.get("status", "unknown")

            if bot_status_value:
                st.caption(f"Strategy: {format_label(bot_status_value, STRATEGY_LABELS)}")

            archive_disabled = docker_status == "missing" or (
                docker_status == "running" and bot_status_value != "stopped"
            )

            strategy_label = "—"
            strategy_action = None
            strategy_available = mqtt_status in {"connected", "stale"}
            if strategy_available:
                if bot_status_value == "stopped":
                    strategy_label = "▶️ Start Strategy"
                    strategy_action = lambda name=bot_name: start_bot(name)
                else:
                    strategy_label = "⏹️ Stop Strategy"
                    strategy_action = lambda name=bot_name: stop_bot(name)
            elif bot_status_value == "stopped":
                strategy_label = "▶️ Start Strategy"
            else:
                strategy_label = "⏹️ Stop Strategy"

            container_label = "—"
            container_action = None
            if docker_status == "running":
                container_label = "⛔ Stop Container"
                container_action = lambda name=bot_name: stop_container(name)
            elif docker_status in {"exited", "created", "dead"}:
                container_label = "▶️ Start Container"
                container_action = lambda name=bot_name: start_container(name)
            elif docker_status == "missing":
                container_label = "➕ Launch New"
                container_action = lambda: st.switch_page("frontend/pages/orchestration/launch_bot_v2/app.py")

            action_cols = st.columns(4)
            with action_cols[0]:
                if st.button(
                    strategy_label,
                    key=f"strategy_{bot_name}",
                    use_container_width=True,
                    disabled=strategy_action is None,
                ):
                    if strategy_action:
                        strategy_action()
            with action_cols[1]:
                if st.button(
                    container_label,
                    key=f"container_{bot_name}",
                    use_container_width=True,
                    disabled=container_action is None,
                ):
                    if container_action:
                        container_action()
            with action_cols[2]:
                if st.button(
                    "📦 Archive",
                    key=f"archive_{bot_name}",
                    use_container_width=True,
                    disabled=archive_disabled,
                ):
                    archive_bot(bot_name, docker_status)
            with action_cols[3]:
                if st.button("📜 Logs Page", key=f"open_logs_{bot_name}", use_container_width=True):
                    st.session_state.logs_selected_container = bot_name
                    st.switch_page("frontend/pages/orchestration/logs/app.py")

            if not show_details:
                continue

            if bot_status.get("status") != "success":
                st.caption("Strategy status unavailable.")
                continue

            bot_state = bot_data.get("status", "unknown")
            performance = bot_data.get("performance", {})

            controller_configs = []
            if bot_state in {"running", "idle"}:
                try:
                    controller_configs = backend_api_client.controllers.get_bot_controller_configs(bot_name)
                    controller_configs = controller_configs if controller_configs else []
                except Exception as e:
                    st.warning(f"Could not fetch controller configs for {bot_name}: {e}")
                    controller_configs = []

            if performance:
                render_controller_tables(bot_name, performance, controller_configs)
            elif bot_state in {"running", "idle"}:
                st.caption("No controller performance data available yet.")

            render_logs(bot_name, bot_data)


def render_controller_tables(bot_name: str, performance: Dict[str, Any], controller_configs: List[Dict[str, Any]]):
    (
        active_controllers,
        stopped_controllers,
        error_controllers,
        total_global_pnl_quote,
        total_volume_traded,
        total_unrealized_pnl_quote,
        config_map,
    ) = build_controller_rows(performance, controller_configs)

    total_global_pnl_pct = total_global_pnl_quote / total_volume_traded if total_volume_traded > 0 else 0

    metric_cols = st.columns(4)
    with metric_cols[0]:
        st.metric("🏦 NET PNL", f"${total_global_pnl_quote:.2f}")
    with metric_cols[1]:
        st.metric("💹 Unrealized PNL", f"${total_unrealized_pnl_quote:.2f}")
    with metric_cols[2]:
        st.metric("📊 NET PNL (%)", f"{total_global_pnl_pct:.2%}")
    with metric_cols[3]:
        st.metric("💸 Volume Traded", f"${total_volume_traded:.2f}")

    st.caption(
        f"Controllers: {len(active_controllers)} active · {len(stopped_controllers)} paused · {len(error_controllers)} error"
    )

    if active_controllers:
        st.success("🚀 Active Controllers")
        active_df = pd.DataFrame(active_controllers)
        edited_active_df = st.data_editor(
            active_df,
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "Select",
                    help="Select controllers to stop",
                    default=False,
                ),
                "_controller_id": None,
            },
            disabled=[col for col in active_df.columns if col != "Select"],
            hide_index=True,
            use_container_width=True,
            key=f"active_table_{bot_name}",
        )

        selected_active = [
            row["_controller_id"]
            for _, row in edited_active_df.iterrows()
            if row["Select"]
        ]

        if selected_active:
            if st.button(
                f"⏹️ Stop Selected ({len(selected_active)})",
                key=f"stop_active_{bot_name}",
                type="secondary",
            ):
                                with st.spinner(f"Stopping {len(selected_active)} controller(s)..."):
                                    stop_controllers(bot_name, selected_active)

    if stopped_controllers:
        st.warning("💤 Paused Controllers")
        stopped_df = pd.DataFrame(stopped_controllers)
        edited_stopped_df = st.data_editor(
            stopped_df,
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "Select",
                    help="Select controllers to start",
                    default=False,
                ),
                "_controller_id": None,
            },
            disabled=[col for col in stopped_df.columns if col != "Select"],
            hide_index=True,
            use_container_width=True,
            key=f"stopped_table_{bot_name}",
        )

        selected_stopped = [
            row["_controller_id"]
            for _, row in edited_stopped_df.iterrows()
            if row["Select"]
        ]

        if selected_stopped:
            if st.button(
                f"▶️ Start Selected ({len(selected_stopped)})",
                key=f"start_stopped_{bot_name}",
                type="primary",
            ):
                                with st.spinner(f"Starting {len(selected_stopped)} controller(s)..."):
                                    start_controllers(bot_name, selected_stopped)

    if error_controllers:
        st.error("💀 Controllers with Errors")
        error_df = pd.DataFrame(error_controllers)
        st.dataframe(error_df, use_container_width=True, hide_index=True)

    if config_map:
        missing = [
            config_id
            for config_id in config_map.keys()
            if config_id not in performance
        ]
        if missing:
            st.info(f"Controllers configured but not reporting yet: {', '.join(missing)}")

    lp_rows = build_lp_position_rows(performance, config_map)
    if lp_rows:
        with st.expander("LP Positions", expanded=True):
            lp_df = pd.DataFrame(lp_rows)
            st.dataframe(lp_df, use_container_width=True, hide_index=True)


def render_logs(bot_name: str, bot_data: Dict[str, Any]):
    with st.expander("Logs", expanded=False):
        log_tabs = st.tabs(["Bot Logs", "Instance Logs"])

        with log_tabs[0]:
            error_logs = bot_data.get("error_logs", [])
            general_logs = bot_data.get("general_logs", [])

            log_type = st.radio(
                "Stream",
                options=["Errors", "General"],
                horizontal=True,
                index=1,
                key=f"bot_log_type_{bot_name}",
            )
            log_lines = st.selectbox(
                "Lines",
                options=[50, 100, 200],
                index=2,
                key=f"bot_log_lines_{bot_name}",
            )
            search = st.text_input(
                "Search",
                placeholder="Filter bot logs",
                key=f"bot_log_search_{bot_name}",
            )

            if log_type == "Errors":
                lines = format_error_logs(error_logs)
            else:
                lines = format_general_logs(general_logs)

            lines = filter_logs(lines, search, log_lines)
            if lines:
                st.code("\n".join(lines), language="log")
            else:
                st.info("No bot logs available for the selected filters.")

        with log_tabs[1]:
            log_type_label = st.radio(
                "Stream",
                options=["Errors", "Hummingbot", "Bot"],
                horizontal=True,
                index=2,
                key=f"instance_log_type_{bot_name}",
            )
            log_lines = st.selectbox(
                "Lines",
                options=[50, 100, 200, 500],
                index=2,
                key=f"instance_log_lines_{bot_name}",
            )
            search = st.text_input(
                "Search",
                placeholder="Filter instance logs",
                key=f"instance_log_search_{bot_name}",
            )

            log_type_map = {
                "Errors": "errors",
                "Hummingbot": "hummingbot",
                "Bot": "bot",
            }
            log_type_value = log_type_map.get(log_type_label, "bot")

            cache_key = f"instance_logs_cache_{bot_name}"
            cache = st.session_state.get(cache_key, {})
            cached_text = cache.get("text", "")
            cached_type = cache.get("type")
            cached_lines = cache.get("lines")
            cached_at = cache.get("fetched_at")

            fetch_now = st.button(
                "Refresh instance logs",
                key=f"refresh_instance_logs_{bot_name}",
                use_container_width=True,
            )

            if fetch_now or not cached_text or cached_type != log_type_value or cached_lines != log_lines:
                logs_response = backend_api_request(
                    "GET",
                    f"/bot-orchestration/instances/{bot_name}/logs",
                    params={"log_type": log_type_value, "tail": log_lines},
                )

                if not logs_response.get("ok"):
                    status_code = logs_response.get("status_code")
                    if status_code == 401:
                        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                    elif status_code == 404:
                        st.error("Instance log file not found.")
                    else:
                        st.error(logs_response.get("error", "Failed to fetch instance logs."))
                    return

                cached_text = logs_response.get("data", {}).get("logs", "")
                cached_type = log_type_value
                cached_lines = log_lines
                cached_at = datetime.now()
                st.session_state[cache_key] = {
                    "text": cached_text,
                    "type": cached_type,
                    "lines": cached_lines,
                    "fetched_at": cached_at,
                }

            if not cached_text:
                st.info("No instance logs available.")
                return

            if cached_at:
                st.caption(f"Last fetched: {cached_at.strftime('%Y-%m-%d %H:%M:%S')}")

            log_entries = cached_text.strip().split("\n")
            log_entries = filter_logs(log_entries, search, log_lines)
            if not log_entries:
                st.info("No instance logs match the current filters.")
                return

            visible_logs = "\n".join(log_entries)
            st.code(visible_logs, language="log")
            st.download_button(
                "Download logs (txt)",
                data=visible_logs,
                file_name=f"{bot_name}-logs.txt",
                mime="text/plain",
                use_container_width=True,
            )


# Page Header
st.title("🦅 Hummingbot Instances")
st.caption("Manage container lifecycle, controller health, and logs in one place.")

status_placeholder = st.empty()

action_message = st.session_state.pop("last_action_message", None)
action_level = st.session_state.pop("last_action_level", "success")
if action_message:
    if action_level == "success":
        st.success(action_message)
    elif action_level == "warning":
        st.warning(action_message)
    else:
        st.error(action_message)


@st.fragment(run_every=REFRESH_INTERVAL)
def show_bot_instances():
    """Fragment to display bot instances with auto-refresh."""
    try:
        instances_response = backend_api_request("GET", "/bot-orchestration/instances")

        if not instances_response.get("ok"):
            status_code = instances_response.get("status_code")
            if status_code == 401:
                st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
            else:
                st.error("Failed to fetch instances. Verify the backend API and Docker connectivity.")
            return

        instances = instances_response.get("data", {}).get("data", {}).get("instances", [])

        if not instances:
            status_placeholder.info("No bot instances found. Deploy a bot to see it here.")
            return

        counts = {
            "running": sum(1 for inst in instances if inst.get("health_state") == "running"),
            "degraded": sum(1 for inst in instances if inst.get("health_state") == "degraded"),
            "stopped": sum(1 for inst in instances if inst.get("health_state") == "stopped"),
            "orphaned": sum(1 for inst in instances if inst.get("health_state") == "orphaned"),
        }

        status_placeholder.info(
            f"🔄 Auto-refreshing every {REFRESH_INTERVAL} seconds · "
            f"Running {counts['running']} · Degraded {counts['degraded']} · "
            f"Stopped {counts['stopped']} · Orphaned {counts['orphaned']}"
        )

        render_overview(instances)

    except Exception as e:
        st.error(f"Failed to connect to backend: {e}")
        st.info("Please make sure the backend is running and accessible.")


show_bot_instances()
