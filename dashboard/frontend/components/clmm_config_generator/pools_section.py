from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.st_utils import backend_api_request


def is_gateway_connector(connector_name: str) -> bool:
    return isinstance(connector_name, str) and "/" in connector_name


def connector_base_name(connector_name: str) -> str:
    if not connector_name:
        return ""
    return connector_name.split("/", 1)[0]


def connector_pool_type(connector_name: str) -> Optional[str]:
    if not connector_name or "/" not in connector_name:
        return None
    _, suffix = connector_name.split("/", 1)
    suffix = suffix.strip().lower()
    return suffix if suffix in {"clmm", "amm"} else None


def extract_network_value(network_id: str) -> Optional[str]:
    if not network_id:
        return None
    if "-" in network_id:
        _, network_value = network_id.split("-", 1)
        return network_value
    return network_id


def get_gateway_networks() -> List[Dict]:
    if "gen_gateway_networks" in st.session_state:
        return st.session_state["gen_gateway_networks"]
    response = backend_api_request("GET", "/gateway/networks")
    networks = response.get("data", {}).get("networks", []) if response.get("ok") else []
    networks = [item for item in networks if isinstance(item, dict) and item.get("network_id")]
    st.session_state["gen_gateway_networks"] = networks
    return networks


def load_gateway_pools(
    connector_name: str,
    gateway_network_id: str,
    search_term: str,
    limit: int = 50,
    force_refresh: bool = False,
):
    if "gen_pool_cache" not in st.session_state:
        st.session_state["gen_pool_cache"] = {}
    cache = st.session_state["gen_pool_cache"]

    connector_base = connector_base_name(connector_name)
    if not connector_base:
        return [], "Missing connector name"

    network_value = extract_network_value(gateway_network_id or "") if gateway_network_id else None
    pool_type = connector_pool_type(connector_name)
    cache_key = f"{connector_base}:{network_value}:{pool_type}:{search_term}:{limit}"

    if not force_refresh and cache_key in cache:
        return cache[cache_key], None

    params = {"connector_name": connector_base}
    if network_value:
        params["network"] = network_value
    if pool_type:
        params["pool_type"] = pool_type
    if search_term:
        params["search"] = search_term
    response = backend_api_request("GET", "/gateway/pools", params=params, timeout=30)
    if response.get("ok"):
        pools = response.get("data", []) or []
        if limit and len(pools) > limit:
            pools = pools[:limit]
        cache[cache_key] = pools
        return pools, None
    return [], response.get("error", "Failed to fetch Gateway pools.")


def render_pool_section(connector_name: str) -> List[Dict[str, Optional[str]]]:
    st.info("3) Select pools from Gateway")

    pool_rows = st.session_state.get("gen_pool_rows", [])

    if is_gateway_connector(connector_name):
        networks = get_gateway_networks()
        network_map = {item["network_id"]: item for item in networks}
        network_options = ["(select network)"] + sorted(network_map.keys())
        selected_network_id = st.selectbox(
            "Gateway Network",
            options=network_options,
            index=0,
            key="gen_gateway_network",
        )

        search_term = st.text_input("Pool search (optional)", key="gen_pool_search")
        load_clicked = st.button("Load pools", use_container_width=True, key="gen_pool_load")

        pools = st.session_state.get("gen_pool_results", [])
        pool_error = None
        if load_clicked:
            if selected_network_id == "(select network)":
                pool_error = "Select a network before loading pools."
                pools = []
            else:
                pools, pool_error = load_gateway_pools(
                    connector_name=connector_name,
                    gateway_network_id=selected_network_id,
                    search_term=search_term,
                    force_refresh=True,
                )
            st.session_state["gen_pool_results"] = pools

        if pool_error:
            st.warning(pool_error)

        if pools:
            pool_rows_data = []
            for pool in pools:
                row = generator.pool_to_override_row(pool)
                pool_rows_data.append({
                    "Select": False,
                    "Trading Pair": row.get("trading_pair") or "-",
                    "Pool Address": row.get("pool_address") or "-",
                    "Pool Trading Pair": row.get("pool_trading_pair") or "-",
                })
            pool_df = pd.DataFrame(pool_rows_data)
            pool_edited = st.data_editor(
                pool_df,
                column_config={
                    "Select": st.column_config.CheckboxColumn("Select", default=False),
                },
                disabled=[col for col in pool_df.columns if col != "Select"],
                hide_index=True,
                use_container_width=True,
                key="gen_pool_table",
            )

            if st.button("Add selected pools", use_container_width=True, key="gen_pool_add"):
                new_rows = []
                for _, row in pool_edited.iterrows():
                    if not row["Select"]:
                        continue
                    new_rows.append({
                        "trading_pair": row["Trading Pair"],
                        "pool_trading_pair": row["Pool Trading Pair"],
                        "pool_address": row["Pool Address"],
                    })
                if new_rows:
                    pool_rows = generator.merge_override_rows(pool_rows, new_rows)
                    st.session_state["gen_pool_rows"] = pool_rows
                else:
                    st.caption("Select at least one pool to add.")

        if pool_rows:
            st.caption("Selected pools")
            pool_rows_df = pd.DataFrame([
                {
                    "Remove": False,
                    "Trading Pair": row.get("trading_pair") or "-",
                    "Pool Trading Pair": row.get("pool_trading_pair") or "-",
                    "Pool Address": row.get("pool_address") or "-",
                }
                for row in pool_rows
            ])
            pool_rows_edited = st.data_editor(
                pool_rows_df,
                column_config={
                    "Remove": st.column_config.CheckboxColumn("Remove", default=False),
                },
                disabled=[col for col in pool_rows_df.columns if col != "Remove"],
                hide_index=True,
                use_container_width=True,
                key="gen_pool_rows_table",
            )

            if st.button("Remove selected pools", use_container_width=True, key="gen_pool_remove"):
                remaining = []
                for row in pool_rows_edited.to_dict("records"):
                    if row.get("Remove"):
                        continue
                    remaining.append({
                        "trading_pair": row.get("Trading Pair"),
                        "pool_trading_pair": row.get("Pool Trading Pair"),
                        "pool_address": row.get("Pool Address"),
                    })
                st.session_state["gen_pool_rows"] = remaining
                pool_rows = remaining
    else:
        st.caption("Template connector is not a Gateway connector. Use manual overrides below.")

    return pool_rows
