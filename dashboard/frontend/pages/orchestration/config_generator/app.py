import re
from typing import Dict, List

import pandas as pd
import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.st_utils import backend_api_request, get_backend_api_client, initialize_st_page

initialize_st_page(show_readme=False)

st.title("Config Generator")
st.subheader("Generate controller configs for new trading pairs")

backend_api_client = get_backend_api_client()

try:
    controller_configs = backend_api_client.controllers.list_controller_configs()
except Exception as e:
    st.error(f"Failed to fetch controller configs: {e}")
    st.stop()

config_map: Dict[str, Dict] = {}
rows: List[Dict] = []

for config in controller_configs:
    if not isinstance(config, dict):
        continue
    config_id = config.get("id") or config.get("config", {}).get("id")
    if not config_id:
        continue
    config_data = config.get("config", config)
    config_map[config_id] = config_data

    rows.append({
        "Select": False,
        "Config ID": config_id,
        "Controller": config_data.get("controller_name", config_id),
        "Type": config_data.get("controller_type", "-"),
        "Connector": config_data.get("connector_name", "-"),
        "Trading Pair": config_data.get("trading_pair", "-"),
    })

if not rows:
    st.warning("No controller configurations available. Create configs first.")
    st.stop()

st.info("1) Select base configs to generate new variants")
df = pd.DataFrame(rows)
edited_df = st.data_editor(
    df,
    column_config={
        "Select": st.column_config.CheckboxColumn(
            "Select",
            help="Choose base configs to generate from",
            default=False,
        ),
    },
    disabled=[col for col in df.columns if col != "Select"],
    hide_index=True,
    use_container_width=True,
    key="config_generator_table",
)

selected_config_ids = [
    row["Config ID"]
    for _, row in edited_df.iterrows()
    if row["Select"]
]

if not selected_config_ids:
    st.caption("Select at least one base config to continue.")
    st.stop()

st.divider()

st.info("2) Paste trading pairs (one per line) or CSV rows")
st.caption("Format: TRADING_PAIR[,POOL_TRADING_PAIR,POOL_ADDRESS]")
st.caption("If a base config includes pool_address and you change trading_pair, provide a pool_address.")
raw_overrides = st.text_area(
    "Trading Pair Overrides",
    placeholder="ETH-USDT\nSOL-USDT, SOL-USDT, 0xpooladdress",
    height=160,
    key="config_generator_overrides",
)

override_rows = generator.parse_override_rows(raw_overrides)
if not override_rows:
    st.caption("Enter at least one trading pair to generate configs.")
    st.stop()

st.divider()

st.info("3) Preview generated configs")

existing_ids = set(config_map.keys())
plan_rows: List[Dict] = []
plans_ready: List[Dict] = []

for base_id in selected_config_ids:
    base_config = config_map.get(base_id)
    if not isinstance(base_config, dict):
        continue

    for row in override_rows:
        errors = generator.validate_override_row(base_config, row)
        trading_pair = row.get("trading_pair") or ""
        normalized_pair = re.sub(r"[^a-zA-Z0-9]+", "-", trading_pair.strip()).strip("-").lower()
        normalized_pair = normalized_pair or "pair"

        candidate = f"{base_id}--{normalized_pair}"
        new_id = candidate
        suffix = 1
        while new_id in existing_ids:
            new_id = f"{candidate}-{suffix}"
            suffix += 1
        existing_ids.add(new_id)

        payload = generator.build_override_payload(base_config, row)
        payload["id"] = new_id

        status = "Ready" if not errors else "Error"
        plan_rows.append({
            "Base Config": base_id,
            "New Config": new_id,
            "Trading Pair": payload.get("trading_pair", "-"),
            "Pool Trading Pair": payload.get("pool_trading_pair", "-"),
            "Pool Address": payload.get("pool_address", "-"),
            "Status": status,
            "Notes": "; ".join(errors) if errors else "-",
        })

        if not errors:
            plans_ready.append({
                "config_id": new_id,
                "payload": payload,
            })

if plan_rows:
    st.dataframe(pd.DataFrame(plan_rows), use_container_width=True, hide_index=True)

ready_count = len(plans_ready)
if ready_count == 0:
    st.warning("Fix errors above before generating configs.")
    st.stop()

st.success(f"{ready_count} config(s) ready to generate")

col1, col2 = st.columns(2)

with col1:
    if st.button("Generate Configs", type="primary", use_container_width=True):
        created = []
        failures = []
        for plan in plans_ready:
            config_id = plan["config_id"]
            response = backend_api_request(
                "POST",
                f"/controllers/configs/{config_id}",
                json_body=plan["payload"],
            )
            if response.get("ok"):
                created.append(config_id)
            else:
                failures.append({
                    "config_id": config_id,
                    "error": response.get("error", "Failed to create config."),
                })

        if created:
            st.success(f"Created {len(created)} configs.")
        if failures:
            st.error("Some configs failed to create.")
            st.dataframe(pd.DataFrame(failures), use_container_width=True, hide_index=True)

        if created:
            st.rerun()

with col2:
    if st.button("Go to Deploy V2", use_container_width=True):
        st.switch_page("frontend/pages/orchestration/launch_bot_v2/app.py")
