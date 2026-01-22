import re
from datetime import datetime, timedelta

import streamlit as st

from frontend.st_utils import backend_api_request, initialize_st_page

initialize_st_page(icon="📜", show_readme=False)

st.title("Logs")
st.subheader("Gateway and container logs")

FOCUS_FILTERS = {
    "State changes": ["state change", "transition", "controller state change"],
    "Rebalance": ["rebalance", "out_of_range", "out-of-range", "reopen", "recenter"],
    "Swaps": ["swap", "router", "amm"],
    "LP actions": ["open", "close", "add_liquidity", "remove_liquidity", "liquidity"],
    "Stop loss": ["stop_loss", "stop loss", "drawdown"],
    "Errors": ["error", "failed", "exception", "traceback"],
}

running_response = backend_api_request("GET", "/docker/active-containers")
exited_response = backend_api_request("GET", "/docker/exited-containers")

if not running_response.get("ok") and not exited_response.get("ok"):
    st.error("Docker API is unreachable. Verify the backend API and Docker connectivity.")
    st.stop()

container_rows = {}

if running_response.get("ok"):
    running_data = running_response.get("data", [])
    if isinstance(running_data, list):
        for item in running_data:
            name = item.get("name")
            if not name:
                continue
            container_rows[name] = {
                "name": name,
                "status": "running",
                "image": item.get("image", "unknown"),
            }

if exited_response.get("ok"):
    exited_data = exited_response.get("data", [])
    if isinstance(exited_data, list):
        for item in exited_data:
            name = item.get("name")
            if not name or name in container_rows:
                continue
            container_rows[name] = {
                "name": name,
                "status": "exited",
                "image": item.get("image", "unknown"),
            }

containers = list(container_rows.values())
containers.sort(key=lambda row: row["name"])

if not containers:
    st.info("No containers found.")
    st.stop()

st.divider()
st.subheader("Source")

filter_cols = st.columns([1, 2])
with filter_cols[0]:
    status_filter = st.selectbox("Status", options=["All", "Running", "Exited"])

with filter_cols[1]:
    name_search = st.text_input("Search", placeholder="Filter containers by name")

filtered = containers
if status_filter == "Running":
    filtered = [row for row in filtered if row["status"] == "running"]
elif status_filter == "Exited":
    filtered = [row for row in filtered if row["status"] == "exited"]

if name_search:
    search_lower = name_search.lower()
    filtered = [row for row in filtered if search_lower in row["name"].lower()]

if not filtered:
    st.info("No containers match the current filters.")
    st.stop()

default_container = "gateway" if any(row["name"] == "gateway" for row in filtered) else filtered[0]["name"]
container_options = [row["name"] for row in filtered]

selected_container = st.selectbox(
    "Container",
    options=container_options,
    index=container_options.index(default_container) if default_container in container_options else 0,
)

selected_meta = container_rows.get(selected_container, {})
meta_cols = st.columns(3)
with meta_cols[0]:
    st.metric("Status", selected_meta.get("status", "unknown"))
with meta_cols[1]:
    st.metric("Image", selected_meta.get("image", "unknown"))
with meta_cols[2]:
    st.metric("Container", selected_container)

st.divider()
st.subheader("Log Viewer")

controls_cols = st.columns([1, 1, 1, 2])
with controls_cols[0]:
    log_lines = st.selectbox("Lines", options=[50, 100, 200, 500], index=1)
with controls_cols[1]:
    time_window = st.selectbox("Window", options=["All", "5m", "15m", "1h", "6h", "24h"], index=0)
with controls_cols[2]:
    log_level = st.selectbox("Level", options=["ALL", "error", "warning", "info", "debug"])
with controls_cols[3]:
    log_search = st.text_input("Search logs", placeholder="Filter logs by keyword")

focus_tags = st.multiselect(
    "Focus",
    options=list(FOCUS_FILTERS.keys()),
    placeholder="Optional signal filters",
)
st.caption("Time window applies to lines with timestamps; others are kept.")

if st.button("Refresh Logs", use_container_width=True):
    st.rerun()

logs_response = backend_api_request(
    "GET",
    f"/docker/containers/{selected_container}/logs",
    params={"tail": log_lines},
)

if not logs_response.get("ok"):
    status_code = logs_response.get("status_code")
    if status_code == 401:
        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
    elif status_code == 404:
        st.error("Logs endpoint not available. Recreate the hummingbot-api container to load the latest API.")
    else:
        st.error(logs_response.get("error", "Failed to fetch logs."))
    st.stop()

logs_text = logs_response.get("data", {}).get("logs", "")
if not logs_text:
    st.info("No logs available for this container.")
    st.stop()

log_entries = logs_text.strip().split("\n")

def parse_log_timestamp(line: str):
    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:[,.](?P<ms>\d{1,6}))?",
        line,
    )
    if not match:
        return None
    ts_raw = f"{match.group('date')} {match.group('time')}"
    try:
        ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    ms = match.group("ms")
    if ms:
        ts = ts.replace(microsecond=int(ms.ljust(6, "0")))
    return ts

if time_window != "All":
    now = datetime.now()
    delta_map = {
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
    }
    cutoff = now - delta_map.get(time_window, timedelta())
    filtered_entries = []
    for line in log_entries:
        ts = parse_log_timestamp(line)
        if ts is None or ts >= cutoff:
            filtered_entries.append(line)
    log_entries = filtered_entries

if log_level != "ALL":
    level_lower = log_level.lower()
    if level_lower == "warning":
        log_entries = [
            line for line in log_entries
            if "warning" in line.lower() or "warn" in line.lower()
        ]
    else:
        log_entries = [line for line in log_entries if level_lower in line.lower()]

if log_search:
    search_lower = log_search.lower()
    log_entries = [line for line in log_entries if search_lower in line.lower()]

if focus_tags:
    focus_patterns = []
    for tag in focus_tags:
        focus_patterns.extend(FOCUS_FILTERS.get(tag, []))
    focus_patterns = [pattern.lower() for pattern in focus_patterns if pattern]
    if focus_patterns:
        log_entries = [
            line for line in log_entries
            if any(pattern in line.lower() for pattern in focus_patterns)
        ]

if not log_entries:
    st.info("No logs match the current filters.")
    st.stop()

visible_logs = "\n".join(log_entries[-log_lines:])
st.code(visible_logs, language="log")
st.download_button(
    "Download logs (txt)",
    data=visible_logs,
    file_name=f"{selected_container}-logs.txt",
    mime="text/plain",
    use_container_width=True,
)

info_count = sum(1 for line in log_entries if "info" in line.lower())
warn_count = sum(1 for line in log_entries if "warning" in line.lower() or "warn" in line.lower())
error_count = sum(1 for line in log_entries if "error" in line.lower())
debug_count = sum(1 for line in log_entries if "debug" in line.lower())

stats_cols = st.columns(4)
with stats_cols[0]:
    st.metric("INFO", info_count)
with stats_cols[1]:
    st.metric("WARNING", warn_count)
with stats_cols[2]:
    st.metric("ERROR", error_count)
with stats_cols[3]:
    st.metric("DEBUG", debug_count)
