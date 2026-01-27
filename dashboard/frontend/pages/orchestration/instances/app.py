from datetime import datetime
import time
from typing import Any, Dict, List, Optional, Tuple

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


def split_trading_pair(trading_pair: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not trading_pair or "-" not in trading_pair:
        return None, None
    base, quote = trading_pair.split("-", 1)
    return base, quote


def format_quote_value(value: Optional[float], quote_symbol: Optional[str], precision: int = 2) -> str:
    formatted = format_number(value, precision)
    if formatted == "-":
        return "-"
    unit = quote_symbol or "Quote"
    return f"{formatted} {unit}"


def format_timestamp_age(timestamp: Optional[float]) -> str:
    if timestamp is None:
        return "-"
    try:
        age = max(0, time.time() - float(timestamp))
    except (TypeError, ValueError):
        return "-"
    return format_age(age) or "-"


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


def is_not_found_error(error: Exception) -> bool:
    message = str(error).lower()
    return "404" in message and "not found" in message


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


def stop_strategy(bot_name: str):
    try:
        controller_configs = backend_api_client.controllers.get_bot_controller_configs(bot_name)
    except Exception as e:
        st.error(f"Failed to load controllers for {bot_name}: {e}")
        return
    controller_ids = []
    for config in controller_configs or []:
        if not isinstance(config, dict):
            continue
        controller_name = config.get("_config_name") or config.get("id")
        if controller_name:
            controller_ids.append(controller_name)
    if not controller_ids:
        st.warning(f"No controllers found for {bot_name}.")
        return
    stop_controllers(bot_name, controller_ids)


def start_strategy(bot_name: str):
    try:
        controller_configs = backend_api_client.controllers.get_bot_controller_configs(bot_name)
    except Exception as e:
        st.error(f"Failed to load controllers for {bot_name}: {e}")
        return
    controller_ids = []
    for config in controller_configs or []:
        if not isinstance(config, dict):
            continue
        controller_name = config.get("_config_name") or config.get("id")
        if controller_name:
            controller_ids.append(controller_name)
    if not controller_ids:
        st.warning(f"No controllers found for {bot_name}.")
        return
    start_controllers(bot_name, controller_ids)


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

    mode = custom_info.get("mode")
    state = mode or custom_info.get("state") or custom_info.get("controller_state") or custom_info.get("hedge_state")
    if state:
        signals.append(f"State: {state}")
    mode_rule = custom_info.get("mode_rule")
    if mode_rule:
        notes.append(f"Rule: {mode_rule}")

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

    lp_active = custom_info.get("active_lp_count")
    swap_active = custom_info.get("active_swap_count")
    if lp_active is None or swap_active is None:
        positions = custom_info.get("positions")
        if isinstance(positions, dict):
            if lp_active is None:
                lp_active = positions.get("lp_active")
            if swap_active is None:
                swap_active = positions.get("swap_active")
    if lp_active is not None:
        signals.append(f"LP: {lp_active}")
    if swap_active is not None:
        signals.append(f"Swaps: {swap_active}")

    price = custom_info.get("price")
    if price is not None:
        signals.append(f"Price: {format_number(price, 4)}")

    base = custom_info.get("wallet_base")
    quote = custom_info.get("wallet_quote")
    if base is None and quote is None:
        wallet = custom_info.get("wallet")
        if isinstance(wallet, dict):
            base = wallet.get("base")
            quote = wallet.get("quote")
    if base is not None or quote is not None:
        base_str = format_number(base, 4) if base is not None else "-"
        quote_str = format_number(quote, 4) if quote is not None else "-"
        signals.append(f"Wallet: {base_str}/{quote_str}")

    stop_loss_active = mode == "STOPLOSS"
    rebalance_pending = mode == "REBALANCE"
    flags = custom_info.get("flags")
    if isinstance(flags, dict):
        stop_loss_active = bool(flags.get("stop_loss_active"))
        rebalance_pending = flags.get("rebalance_pending")

    if custom_info.get("stop_loss_active") is not None:
        stop_loss_active = bool(custom_info.get("stop_loss_active"))
    if custom_info.get("rebalance_pending") is not None:
        rebalance_pending = custom_info.get("rebalance_pending")

    if custom_info.get("stoploss_pending_liquidation") is True:
        stop_loss_active = True
    rebalance_count = custom_info.get("rebalance_plan_count")
    if rebalance_count is not None:
        rebalance_pending = rebalance_count

    if custom_info.get("awaiting_balance_refresh") is True:
        signals.append("Balance: syncing")

    if stop_loss_active:
        signals.append("StopLoss: on")
    if rebalance_pending:
        if isinstance(rebalance_pending, (int, float)) and rebalance_pending is not True:
            signals.append(f"Rebalance: {rebalance_pending}")
        else:
            signals.append("Rebalance: on")

    state_reason = custom_info.get("state_reason") or custom_info.get("intent_reason")
    if state_reason:
        notes.append(state_reason)
    cooldown_sec = custom_info.get("stoploss_cooldown_remaining_sec")
    if cooldown_sec is not None and cooldown_sec > 0:
        notes.append(f"StopLoss cooldown: {format_number(cooldown_sec, 0)}s")

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
    total_realized_pnl_quote = 0
    quote_symbols: set = set()

    if not isinstance(performance, dict):
        return (
            active_controllers,
            stopped_controllers,
            error_controllers,
            total_global_pnl_quote,
            total_volume_traded,
            total_unrealized_pnl_quote,
            total_realized_pnl_quote,
            config_map,
        )

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
        _, quote_symbol = split_trading_pair(trading_pair)
        if quote_symbol:
            quote_symbols.add(quote_symbol)
        kill_switch_status = controller_config.get("manual_kill_switch", False)

        realized_pnl_quote = controller_performance.get("realized_pnl_quote", 0)
        unrealized_pnl_quote = controller_performance.get("unrealized_pnl_quote", 0)
        global_pnl_quote = controller_performance.get("global_pnl_quote", 0)
        volume_traded = controller_performance.get("volume_traded", 0)
        if isinstance(custom_info, dict) and "controller_net_pnl_quote" in custom_info:
            def _override_value(key: str, default_value: Any):
                value = custom_info.get(key)
                return default_value if value is None else value

            global_pnl_quote = _override_value("controller_net_pnl_quote", global_pnl_quote)
            unrealized_pnl_quote = _override_value("controller_unrealized_pnl_quote", unrealized_pnl_quote)
            realized_pnl_quote = _override_value("controller_realized_pnl_quote", realized_pnl_quote)
            volume_traded = _override_value("controller_volume_quote", volume_traded)
        nav_quote = custom_info.get("nav_quote")

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
            "ID": controller_config.get("id"),
            "Controller": controller_name,
            "Connector": connector_name,
            "Trading Pair": trading_pair,
            "Signals": signals.get("signals", ""),
            "Notes": signals.get("notes", ""),
            "Realized PNL": format_quote_value(realized_pnl_quote, quote_symbol, 2),
            "Unrealized PNL": format_quote_value(unrealized_pnl_quote, quote_symbol, 2),
            "NET PNL": format_quote_value(global_pnl_quote, quote_symbol, 2),
            "NAV": format_quote_value(nav_quote, quote_symbol, 2) if nav_quote is not None else "-",
            "Volume": format_quote_value(volume_traded, quote_symbol, 2),
            "Close Types": close_types_str,
            "_controller_id": controller,
        }

        total_global_pnl_quote += global_pnl_quote
        total_volume_traded += volume_traded
        total_unrealized_pnl_quote += unrealized_pnl_quote
        total_realized_pnl_quote += realized_pnl_quote

        if kill_switch_status:
            stopped_controllers.append(controller_info)
        else:
            active_controllers.append(controller_info)

    return (
        active_controllers,
        stopped_controllers,
        error_controllers,
        total_global_pnl_quote,
        total_volume_traded,
        total_unrealized_pnl_quote,
        total_realized_pnl_quote,
        quote_symbols,
        config_map,
    )


LP_POSITION_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
.lp-card {
  --lp-ink: #111827;
  --lp-muted: #6b7280;
  --lp-paper: #ffffff;
  --lp-wash: #f8fafc;
  --lp-line: rgba(15, 23, 42, 0.12);
  --lp-good: #059669;
  --lp-warn: #d97706;
  --lp-alert: #dc2626;
  --lp-info: #2563eb;
  --lp-range-fill: #60a5fa;
  --lp-range-out: #f97316;
  background: var(--lp-paper);
  border: 1px solid var(--lp-line);
  border-radius: 18px;
  padding: 16px 18px;
  margin-bottom: 16px;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.06);
  position: relative;
  overflow: hidden;
  font-family: 'IBM Plex Sans', sans-serif;
  color: var(--lp-ink);
}
.lp-card-inner {
  position: relative;
  z-index: 1;
}
.lp-card-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.lp-title {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--lp-ink);
}
.lp-sub {
  font-size: 0.78rem;
  color: var(--lp-muted);
  margin-top: 4px;
}
.lp-state {
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--lp-line);
  color: var(--lp-muted);
  background: var(--lp-wash);
}
.lp-state[data-state="IN_RANGE"] {
  color: var(--lp-good);
  border-color: rgba(5, 150, 105, 0.35);
  background: rgba(5, 150, 105, 0.12);
}
.lp-state[data-state="OUT_OF_RANGE"] {
  color: var(--lp-warn);
  border-color: rgba(217, 119, 6, 0.4);
  background: rgba(217, 119, 6, 0.12);
}
.lp-state[data-state="REBALANCE"] {
  color: var(--lp-info);
  border-color: rgba(37, 99, 235, 0.35);
  background: rgba(37, 99, 235, 0.12);
}
.lp-card-body {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) minmax(280px, 1.2fr);
  gap: 16px;
  margin-top: 16px;
}
.lp-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.lp-metric {
  padding: 8px 10px;
  border-radius: 12px;
  border: 1px solid var(--lp-line);
  background: var(--lp-wash);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.lp-metric.primary {
  border-color: rgba(37, 99, 235, 0.25);
  background: rgba(37, 99, 235, 0.08);
}
.lp-label {
  font-size: 0.66rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--lp-muted);
}
.lp-value {
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--lp-ink);
}
.lp-range {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.lp-range-track {
  position: relative;
  height: 18px;
  border-radius: 999px;
  background: #eef2f7;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}
.lp-range-track.out {
  background: rgba(249, 115, 22, 0.12);
  border-color: rgba(249, 115, 22, 0.35);
}
.lp-range-band {
  position: absolute;
  top: 0;
  bottom: 0;
  border-radius: 999px;
  background: var(--lp-range-fill);
}
.lp-range-price {
  position: absolute;
  top: -6px;
  width: 12px;
  height: 12px;
  background: #111827;
  transform: rotate(45deg);
  border: 2px solid var(--lp-paper);
  box-shadow: 0 0 0 2px rgba(28, 25, 23, 0.14);
}
.lp-range-price.out {
  background: var(--lp-range-out);
  box-shadow: 0 0 0 2px rgba(249, 115, 22, 0.35);
}
.lp-range-labels {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.lp-chip {
  font-size: 0.72rem;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid var(--lp-line);
  background: var(--lp-wash);
  color: var(--lp-muted);
}
.lp-chip.strong {
  color: var(--lp-ink);
  border-color: rgba(28, 25, 23, 0.25);
  background: #ffffff;
}
.lp-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.74rem;
  color: var(--lp-muted);
}
.lp-meta strong {
  color: var(--lp-ink);
  font-weight: 600;
}
.lp-range-fallback {
  font-size: 0.8rem;
  color: var(--lp-muted);
}
@media (max-width: 900px) {
  .lp-card-body {
    grid-template-columns: 1fr;
  }
  .lp-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
"""


def build_lp_positions(performance: Dict[str, Any], config_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(performance, dict):
        return rows

    for controller_id, inner_dict in performance.items():
        if not isinstance(inner_dict, dict):
            continue
        custom_info = inner_dict.get("custom_info", {})
        positions = custom_info.get("lp_positions")
        source = "lp_positions"
        if not isinstance(positions, list) or not positions:
            positions = custom_info.get("active_lp")
            source = "active_lp"
        rebalance_pending = custom_info.get("rebalance_plan_count", 0) > 0
        last_snapshot = custom_info.get("last_lp_snapshot")
        if (not isinstance(positions, list) or not positions) and rebalance_pending:
            if isinstance(last_snapshot, list) and last_snapshot:
                positions = last_snapshot
                source = "last_lp_snapshot"
        if not isinstance(positions, list) or not positions:
            anchor_quote = custom_info.get("anchor_value_quote")
            stoploss_quote = custom_info.get("stoploss_trigger_quote")
            if anchor_quote is None and stoploss_quote is None:
                continue
            controller_config = config_map.get(controller_id, {})
            controller_name = controller_config.get("controller_name", controller_id)
            trading_pair = controller_config.get("trading_pair")
            _, quote_symbol = split_trading_pair(trading_pair)
            rows.append({
                "controller": controller_name,
                "pair": trading_pair or "-",
                "quote_symbol": quote_symbol,
                "state": "NO_LP",
                "position": "-",
                "lower": None,
                "upper": None,
                "price": custom_info.get("price"),
                "base": custom_info.get("wallet_base"),
                "quote": custom_info.get("wallet_quote"),
                "value_quote": custom_info.get("nav_quote"),
                "anchor_quote": anchor_quote,
                "stoploss_quote": stoploss_quote,
                "fee_quote_per_hour": None,
                "out_of_range_since": None,
                "snapshot_source": None,
                "snapshot_ts": None,
            })
            continue

        controller_config = config_map.get(controller_id, {})
        controller_name = controller_config.get("controller_name", controller_id)
        trading_pair = controller_config.get("trading_pair")
        _, quote_symbol = split_trading_pair(trading_pair)
        current_price = custom_info.get("price")
        snapshot_ts = custom_info.get("last_lp_snapshot_ts") if source == "last_lp_snapshot" else None

        for pos in positions:
            if not isinstance(pos, dict):
                continue
            if source in {"active_lp", "last_lp_snapshot"}:
                in_range = pos.get("in_range")
                if pos.get("info_unavailable"):
                    state = "UNKNOWN"
                elif source == "last_lp_snapshot":
                    state = "REBALANCE"
                elif in_range is True:
                    state = "IN_RANGE"
                elif in_range is False:
                    state = "OUT_OF_RANGE"
                else:
                    state = "-"
                rows.append({
                    "controller": controller_name,
                    "pair": trading_pair or "-",
                    "quote_symbol": quote_symbol,
                    "state": state,
                    "position": pos.get("executor_id") or "-",
                    "lower": pos.get("lower_price"),
                    "upper": pos.get("upper_price"),
                    "price": current_price,
                    "base": pos.get("base") if pos.get("base") is not None else pos.get("base_amount"),
                    "quote": pos.get("quote") if pos.get("quote") is not None else pos.get("quote_amount"),
                    "value_quote": pos.get("position_value_quote"),
                    "anchor_quote": pos.get("anchor_value_quote"),
                    "stoploss_quote": pos.get("stoploss_trigger_quote"),
                    "fee_quote_per_hour": pos.get("fee_rate_ewma_quote_per_hour"),
                    "out_of_range_since": pos.get("out_of_range_since"),
                    "snapshot_source": "rebalance" if source == "last_lp_snapshot" else None,
                    "snapshot_ts": snapshot_ts,
                })
                continue
            rows.append({
                "controller": controller_name,
                "pair": trading_pair or "-",
                "quote_symbol": quote_symbol,
                "state": pos.get("state") or "-",
                "position": pos.get("position") or "-",
                "lower": pos.get("lower"),
                "upper": pos.get("upper"),
                "price": current_price,
                "base": pos.get("base"),
                "quote": pos.get("quote"),
                "value_quote": pos.get("value_quote"),
                "anchor_quote": None,
                "stoploss_quote": None,
                "fee_quote_per_hour": None,
                "out_of_range_since": None,
                "snapshot_source": None,
                "snapshot_ts": None,
            })

    return rows


def build_range_bar_html(
    lower: Optional[float],
    upper: Optional[float],
    price: Optional[float],
) -> Tuple[str, Optional[bool]]:
    if lower is None or upper is None or price is None:
        return "<div class='lp-range-fallback'>Range or price unavailable</div>", None
    try:
        lower_val = float(lower)
        upper_val = float(upper)
        price_val = float(price)
    except (TypeError, ValueError):
        return "<div class='lp-range-fallback'>Range or price unavailable</div>", None
    if upper_val <= lower_val:
        return "<div class='lp-range-fallback'>Range invalid</div>", None

    width = upper_val - lower_val
    pad = width * 0.25
    min_val = lower_val - pad
    max_val = upper_val + pad
    span = max_val - min_val
    if span <= 0:
        return "<div class='lp-range-fallback'>Range invalid</div>", None

    def pct(value: float) -> float:
        return max(0.0, min(100.0, ((value - min_val) / span) * 100.0))

    lower_pct = pct(lower_val)
    upper_pct = pct(upper_val)
    price_pct = pct(price_val)
    out_of_range = price_val < lower_val or price_val > upper_val
    track_class = "lp-range-track out" if out_of_range else "lp-range-track"
    price_class = "lp-range-price out" if out_of_range else "lp-range-price"

    return (
        "<div class='lp-range'>"
        f"<div class='{track_class}'>"
        f"<div class='lp-range-band' style='left:{lower_pct:.2f}%; width:{max(0.5, upper_pct - lower_pct):.2f}%;'></div>"
        f"<div class='{price_class}' style='left:{price_pct:.2f}%;'></div>"
        "</div>"
        "<div class='lp-range-labels'>"
        f"<span class='lp-chip'>Low {format_number(lower_val, 6)}</span>"
        f"<span class='lp-chip strong'>Price {format_number(price_val, 6)}</span>"
        f"<span class='lp-chip'>High {format_number(upper_val, 6)}</span>"
        "</div>"
        "</div>"
    ), out_of_range


def render_lp_positions(positions: List[Dict[str, Any]]) -> None:
    if not positions:
        return
    st.markdown(LP_POSITION_CSS, unsafe_allow_html=True)
    for pos in positions:
        state = pos.get("state") or "-"
        state_attr = state if isinstance(state, str) else "-"
        state_label = state_attr.replace("_", " ").title() if state_attr != "-" else "Unknown"

        quote_symbol = pos.get("quote_symbol")
        range_html, out_of_range = build_range_bar_html(pos.get("lower"), pos.get("upper"), pos.get("price"))

        metrics = [
            {"label": "Value", "value": format_quote_value(pos.get("value_quote"), quote_symbol, 4), "primary": True},
            {"label": "Anchor", "value": format_quote_value(pos.get("anchor_quote"), quote_symbol, 4), "primary": False},
            {"label": "StopLoss", "value": format_quote_value(pos.get("stoploss_quote"), quote_symbol, 4), "primary": False},
            {"label": "Fee/hr", "value": format_quote_value(pos.get("fee_quote_per_hour"), quote_symbol, 6), "primary": False},
            {"label": "Base", "value": format_number(pos.get("base"), 6), "primary": False},
            {"label": "Quote", "value": format_number(pos.get("quote"), 6), "primary": False},
        ]

        metrics_html = "".join(
            f"<div class='lp-metric{' primary' if metric['primary'] else ''}'>"
            f"<span class='lp-label'>{metric['label']}</span>"
            f"<span class='lp-value'>{metric['value']}</span></div>"
            for metric in metrics
        )

        snapshot_source = pos.get("snapshot_source")
        snapshot_ts = pos.get("snapshot_ts")
        if state_attr == "NO_LP":
            status_text = "No active LP"
            meta_right = f"Price {format_number(pos.get('price'), 6)}"
        elif snapshot_source == "rebalance":
            status_text = "Rebalance pending"
            snapshot_age = format_timestamp_age(snapshot_ts)
            meta_right = (
                f"Last active {snapshot_age}" if snapshot_age != "-" else f"Price {format_number(pos.get('price'), 6)}"
            )
        else:
            oor_age = format_timestamp_age(pos.get("out_of_range_since"))
            if out_of_range is None:
                status_text = "Range unknown"
                meta_right = f"Price {format_number(pos.get('price'), 6)}"
            else:
                status_text = "Out of range" if out_of_range else "In range"
                meta_right = (
                    f"OOR age {oor_age}" if out_of_range and oor_age != "-" else f"Price {format_number(pos.get('price'), 6)}"
                )
        meta_html = (
            "<div class='lp-meta'>"
            f"<span><strong>{status_text}</strong></span>"
            f"<span>{meta_right}</span>"
            "</div>"
        )

        card_html = (
            "<div class='lp-card'>"
            "<div class='lp-card-inner'>"
            "<div class='lp-card-head'>"
            "<div>"
            f"<div class='lp-title'>{pos.get('controller')} / {pos.get('pair')}</div>"
            f"<div class='lp-sub'>Position {pos.get('position')}</div>"
            "</div>"
            f"<div class='lp-state' data-state='{state_attr}'>{state_label}</div>"
            "</div>"
            "<div class='lp-card-body'>"
            f"<div class='lp-metrics'>{metrics_html}</div>"
            f"<div>{range_html}{meta_html}</div>"
            "</div>"
            "</div>"
            "</div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)


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

            container_label = "—"
            container_action = None
            container_type = "secondary"
            if docker_status == "running":
                if bot_status_value == "stopped":
                    container_label = "▶️ Start Strategy"
                    container_action = lambda name=bot_name: start_strategy(name)
                    container_type = "primary"
                elif bot_status_value == "stopping":
                    container_label = "⏳ Stopping"
                    container_action = None
                else:
                    container_label = "⛔ Stop Strategy"
                    container_action = lambda name=bot_name: stop_strategy(name)
                    container_type = "secondary"
            elif docker_status in {"exited", "created", "dead"}:
                container_label = "▶️ Start Container"
                container_action = lambda name=bot_name: start_container(name)
                container_type = "primary"
            elif docker_status == "missing":
                container_label = "➕ Launch New"
                container_action = lambda: st.switch_page("frontend/pages/orchestration/launch_bot_v2/app.py")

            archive_label = "🧯 Stop & Archive" if docker_status == "running" else "🗃️ Archive"

            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button(
                    container_label,
                    key=f"container_{bot_name}",
                    use_container_width=True,
                    disabled=container_action is None,
                    type=container_type,
                ):
                    if container_action:
                        container_action()
            with action_cols[1]:
                if st.button(
                    archive_label,
                    key=f"archive_{bot_name}",
                    use_container_width=True,
                    type="secondary",
                ):
                    archive_bot(bot_name, docker_status)
            with action_cols[2]:
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
                    if not is_not_found_error(e):
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
        total_realized_pnl_quote,
        quote_symbols,
        config_map,
    ) = build_controller_rows(performance, controller_configs)

    total_global_pnl_pct = total_global_pnl_quote / total_volume_traded if total_volume_traded > 0 else 0
    quote_label = "Quote"
    if len(quote_symbols) == 1:
        quote_label = next(iter(quote_symbols))
    elif len(quote_symbols) > 1:
        quote_label = "Mixed"

    metric_cols = st.columns(5)
    with metric_cols[0]:
        st.metric("🏦 NET PNL", f"{total_global_pnl_quote:.2f} {quote_label}")
    with metric_cols[1]:
        st.metric("💹 Unrealized PNL", f"{total_unrealized_pnl_quote:.2f} {quote_label}")
    with metric_cols[2]:
        st.metric("✅ Realized PNL", f"{total_realized_pnl_quote:.2f} {quote_label}")
    with metric_cols[3]:
        st.metric("📊 NET PNL (%)", f"{total_global_pnl_pct:.2%}")
    with metric_cols[4]:
        st.metric("💸 Volume Traded", f"{total_volume_traded:.2f} {quote_label}")

    if len(quote_symbols) > 1:
        st.caption("Totals include mixed quote currencies; per-controller values are in their own quote units.")

    st.caption(
        f"Controllers: {len(active_controllers)} active · {len(stopped_controllers)} paused · {len(error_controllers)} error"
    )

    if active_controllers:
        st.success("🚀 Active Controllers")
        active_df = pd.DataFrame(active_controllers).drop(columns=["_controller_id"], errors="ignore")
        st.dataframe(active_df, use_container_width=True, hide_index=True)

    if stopped_controllers:
        st.warning("💤 Paused Controllers")
        stopped_df = pd.DataFrame(stopped_controllers).drop(columns=["_controller_id"], errors="ignore")
        st.dataframe(stopped_df, use_container_width=True, hide_index=True)

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

    lp_positions = build_lp_positions(performance, config_map)
    if lp_positions:
        with st.expander("LP Positions", expanded=True):
            render_lp_positions(lp_positions)


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
