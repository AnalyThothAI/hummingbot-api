import re
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.st_utils import backend_api_request


def render_manual_overrides() -> List[Dict[str, Optional[str]]]:
    st.caption("Format: TRADING_PAIR[,POOL_TRADING_PAIR,POOL_ADDRESS]")
    st.caption("If a template includes pool_address and you change trading_pair, provide a pool_address.")
    raw_overrides = st.text_area(
        "Trading Pair Overrides",
        placeholder="ETH-USDT\nSOL-USDT, SOL-USDT, 0xpooladdress",
        height=160,
        key="config_generator_overrides",
    )
    return generator.parse_override_rows(raw_overrides)


def build_preview_rows(
    template_id: str,
    base_config: Dict,
    override_rows: List[Dict[str, Optional[str]]],
    existing_ids: set,
    budget_key_auto: bool,
):
    plan_rows: List[Dict] = []
    plans_ready: List[Dict] = []

    for row in override_rows:
        errors = generator.validate_override_row(base_config, row)
        trading_pair = row.get("trading_pair") or ""
        normalized_pair = re.sub(r"[^a-zA-Z0-9]+", "-", trading_pair.strip()).strip("-").lower()
        normalized_pair = normalized_pair or "pair"

        candidate = f"{template_id}--{normalized_pair}"
        new_id = candidate
        suffix = 1
        while new_id in existing_ids:
            new_id = f"{candidate}-{suffix}"
            suffix += 1
        existing_ids.add(new_id)

        payload = generator.build_override_payload(base_config, row)
        payload["id"] = new_id
        if budget_key_auto:
            payload["budget_key"] = new_id

        status = "Ready" if not errors else "Error"
        plan_rows.append({
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

    return plan_rows, plans_ready


def render_preview_and_generate(
    template_id: str,
    base_config: Dict,
    override_rows: List[Dict[str, Optional[str]]],
    existing_ids: set,
    budget_key_auto: bool,
):
    st.info("4) Preview generated configs")

    plan_rows, plans_ready = build_preview_rows(
        template_id=template_id,
        base_config=base_config,
        override_rows=override_rows,
        existing_ids=existing_ids,
        budget_key_auto=budget_key_auto,
    )

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
