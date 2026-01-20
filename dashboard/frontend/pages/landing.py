import streamlit as st

from CONFIG import GATEWAY_ENABLED
from frontend.st_utils import initialize_st_page, backend_api_request

initialize_st_page(
    layout="wide",
    show_readme=False,
)

st.title("Hummingbot Dashboard")
st.caption("Operational overview and instance status.")


def status_label(response: dict, ok_label: str, fallback_label: str) -> str:
    if response.get("ok"):
        return ok_label
    status_code = response.get("status_code")
    if status_code == 401:
        return "Unauthorized"
    if status_code is None:
        return "Unreachable"
    return fallback_label


api_root = backend_api_request("GET", "/")
instances_response = backend_api_request("GET", "/bot-orchestration/instances")
mqtt_response = backend_api_request("GET", "/bot-orchestration/mqtt")

if GATEWAY_ENABLED:
    gateway_status_response = backend_api_request("GET", "/gateway/status")
else:
    gateway_status_response = None

api_status = status_label(api_root, "Connected", "Error")

auth_status = "Authorized"
if not instances_response.get("ok"):
    auth_status = status_label(instances_response, "Authorized", "Error")

mqtt_status = "Unavailable"
if mqtt_response.get("ok"):
    mqtt_data = mqtt_response.get("data", {}).get("data", {})
    mqtt_status = "Connected" if mqtt_data.get("mqtt_connected") else "Disconnected"
else:
    mqtt_status = status_label(mqtt_response, "Connected", "Error")

gateway_status = None
if GATEWAY_ENABLED:
    if gateway_status_response and gateway_status_response.get("ok"):
        gateway_running = gateway_status_response.get("data", {}).get("running")
        gateway_status = "Online" if gateway_running else "Offline"
    else:
        gateway_status = status_label(gateway_status_response or {}, "Online", "Error")

instances = instances_response.get("data", {}).get("data", {}).get("instances", []) if instances_response.get("ok") else []
running_count = sum(1 for instance in instances if instance.get("health_state") == "running")
degraded_count = sum(1 for instance in instances if instance.get("health_state") == "degraded")
stopped_count = sum(1 for instance in instances if instance.get("health_state") in {"stopped", "orphaned"})

st.subheader("System Status")
status_cols = st.columns(4 if GATEWAY_ENABLED else 3)

with status_cols[0]:
    st.metric("API", api_status)

with status_cols[1]:
    st.metric("Auth", auth_status)

with status_cols[2]:
    st.metric("MQTT", mqtt_status)

if GATEWAY_ENABLED:
    with status_cols[3]:
        st.metric("Gateway", gateway_status or "Unavailable")

if auth_status == "Unauthorized":
    st.error("API authentication failed. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
elif api_status == "Unreachable":
    st.error("Backend API is unreachable. Verify the API container and network.")

st.divider()

st.subheader("Instances")
summary_cols = st.columns(3)

with summary_cols[0]:
    st.metric("Running", running_count)

with summary_cols[1]:
    st.metric("Degraded", degraded_count)

with summary_cols[2]:
    st.metric("Stopped / Orphaned", stopped_count)

st.divider()

st.subheader("Quick Actions")
action_cols = st.columns(4 if GATEWAY_ENABLED else 3)

with action_cols[0]:
    if st.button("View Instances", use_container_width=True):
        st.switch_page("frontend/pages/orchestration/instances/app.py")

with action_cols[1]:
    if st.button("Deploy V2", use_container_width=True):
        st.switch_page("frontend/pages/orchestration/launch_bot_v2/app.py")

with action_cols[2]:
    if st.button("Credentials", use_container_width=True):
        st.switch_page("frontend/pages/orchestration/credentials/app.py")

if GATEWAY_ENABLED:
    with action_cols[3]:
        if st.button("Gateway", use_container_width=True):
            st.switch_page("frontend/pages/orchestration/gateway/app.py")
