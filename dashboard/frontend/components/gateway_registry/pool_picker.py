from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.components.gateway_registry.common import (
    connector_base_name,
    connector_pool_type,
    extract_network_value,
)
from frontend.components.gateway_registry.ensure import (
    build_add_pool_payload,
    find_token_match,
    pool_exists,
)
from frontend.components.gateway_registry.normalizers import (
    normalize_existing_pool,
    normalize_search_pool,
)
from frontend.components.gateway_registry.validators import (
    is_valid_solana_address,
    normalize_evm_address,
)
from frontend.st_utils import backend_api_request


def _format_api_error(response: Dict, fallback: str) -> str:
    data = response.get("data", {})
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return detail
    return response.get("error") or fallback


def _extract_connector_names(payload: Dict) -> List[str]:
    if isinstance(payload, dict):
        value = payload.get("connectors", payload)
        if isinstance(value, dict):
            return sorted({str(name) for name in value.keys() if name})
        if isinstance(value, list):
            names = []
            for item in value:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("connector") or item.get("connector_name")
                    if name:
                        names.append(str(name))
                elif item:
                    names.append(str(item))
            return sorted(set(names))
    return []


def _get_gateway_networks(prefix: str) -> List[Dict]:
    state_key = f"{prefix}_gateway_networks"
    if state_key in st.session_state:
        return st.session_state[state_key]
    response = backend_api_request("GET", "/gateway/networks")
    networks = response.get("data", {}).get("networks", []) if response.get("ok") else []
    networks = [item for item in networks if isinstance(item, dict) and item.get("network_id")]
    st.session_state[state_key] = networks
    return networks


def _get_gateway_tokens(prefix: str, network_id: str, force_refresh: bool = False) -> List[Dict]:
    state_key = f"{prefix}_gateway_tokens:{network_id}"
    if not force_refresh and state_key in st.session_state:
        return st.session_state[state_key]
    response = backend_api_request("GET", f"/gateway/networks/{network_id}/tokens")
    payload = response.get("data", {})
    tokens = payload.get("tokens", payload if isinstance(payload, list) else [])
    tokens = [item for item in tokens if isinstance(item, dict)]
    st.session_state[state_key] = tokens
    return tokens


def _get_gateway_pools(
    prefix: str,
    connector_name: str,
    network_value: str,
    pool_type: str,
    search_term: str,
    force_refresh: bool = False,
) -> List[Dict]:
    cache_key = f"{prefix}_gateway_pools:{connector_name}:{network_value}:{pool_type}:{search_term}"
    if not force_refresh and cache_key in st.session_state:
        return st.session_state[cache_key]
    response = backend_api_request(
        "GET",
        "/gateway/pools",
        params={
            "connector_name": connector_name,
            "network": network_value,
            "pool_type": pool_type,
            "search": search_term or None,
        },
    )
    pools = response.get("data", []) if response.get("ok") else []
    pools = [item for item in pools if isinstance(item, dict)]
    st.session_state[cache_key] = pools
    return pools


def _maybe_add_token(prefix: str, network_id: str, token_input: str) -> Optional[Dict]:
    if not token_input or not network_id:
        return None
    token_value = token_input.strip()
    if not token_value:
        return None

    tokens = _get_gateway_tokens(prefix, network_id)
    match = find_token_match(tokens, token_value)
    if match:
        return match

    lookup_address = None
    checksum_notice = None
    if token_value.startswith("0x"):
        checksum_address, checksum_notice = normalize_evm_address(token_value)
        if checksum_address is None:
            st.error("Invalid EVM address. Check the address format.")
            return None
        lookup_address = checksum_address
    elif is_valid_solana_address(token_value):
        lookup_address = token_value
    else:
        st.info("Token symbol not found in Gateway. Use address to auto-add.")
        return None

    response = backend_api_request(
        "GET",
        "/metadata/token",
        params={
            "network_id": network_id,
            "address": lookup_address,
        },
    )
    if not response.get("ok"):
        st.error(_format_api_error(response, "Failed to fetch token metadata."))
        return None

    payload = response.get("data", {})
    token = payload.get("token", {}) if isinstance(payload, dict) else {}
    if not isinstance(token, dict):
        st.error("Token metadata not available.")
        return None

    add_payload = {
        "address": lookup_address,
        "symbol": token.get("symbol"),
        "decimals": token.get("decimals"),
    }
    if token.get("name"):
        add_payload["name"] = token.get("name")

    add_response = backend_api_request(
        "POST",
        f"/gateway/networks/{network_id}/tokens",
        json_body=add_payload,
    )
    if not add_response.get("ok"):
        st.error(_format_api_error(add_response, "Failed to add token."))
        return None

    message = add_response.get("data", {}).get(
        "message",
        "Token added. Restart Gateway for changes to take effect.",
    )
    st.success(message)
    if checksum_notice:
        st.info(checksum_notice)

    tokens.append({
        "address": lookup_address,
        "symbol": token.get("symbol"),
        "decimals": token.get("decimals"),
        "name": token.get("name"),
    })
    st.session_state[f"{prefix}_gateway_tokens:{network_id}"] = tokens
    return token


def _resolve_token_for_search(prefix: str, network_id: str, token_input: str) -> Optional[str]:
    if not token_input or not network_id:
        return None
    token_value = token_input.strip()
    if not token_value:
        return None

    tokens = _get_gateway_tokens(prefix, network_id)
    match = find_token_match(tokens, token_value)
    if match and match.get("address"):
        return match.get("address")
    if token_value.startswith("0x"):
        checksum_address, _ = normalize_evm_address(token_value)
        return checksum_address or token_value
    if is_valid_solana_address(token_value):
        return token_value
    return token_value


def _maybe_add_pool(
    *,
    prefix: str,
    connector_name: str,
    network_id: str,
    pool_type: str,
    pool: Dict,
) -> bool:
    pool_address = pool.get("address") or pool.get("pool_address")
    if not pool_address:
        st.error("Selected pool has no address.")
        return False

    network_value = extract_network_value(network_id or "")
    existing_pools = _get_gateway_pools(
        prefix,
        connector_name,
        network_value or "",
        pool_type,
        search_term="",
        force_refresh=False,
    )
    if pool_exists(existing_pools, pool_address):
        return True

    payload = build_add_pool_payload(
        connector_name=connector_name,
        network_id=network_id,
        pool_type=pool_type,
        pool=pool,
    )
    response = backend_api_request("POST", "/gateway/pools", json_body=payload)
    if not response.get("ok"):
        st.error(_format_api_error(response, "Failed to add pool."))
        return False

    message = response.get("data", {}).get("message", "Pool added.")
    st.success(message)
    existing_pools.append(pool)
    cache_key = f"{prefix}_gateway_pools:{connector_name}:{network_value}:{pool_type}:"
    st.session_state[cache_key] = existing_pools
    return True


def render_gateway_pool_picker(
    *,
    prefix: str,
    connector_name: Optional[str],
    allow_connector_override: bool,
    target_rows_key: Optional[str],
    show_existing_toggle: bool = True,
    show_filters: bool = True,
    existing_only: bool = False,
) -> List[Dict]:
    connectors_response = backend_api_request("GET", "/gateway/connectors")
    connectors_payload = connectors_response.get("data", {}) if connectors_response.get("ok") else {}
    connector_options = _extract_connector_names(connectors_payload)

    chosen_connector = connector_name
    if allow_connector_override or not chosen_connector:
        choices = connector_options + ["(custom)"] if connector_options else ["(custom)"]
        selected = st.selectbox("Connector", choices, key=f"{prefix}_connector")
        if selected == "(custom)":
            chosen_connector = st.text_input("Connector Name", key=f"{prefix}_connector_custom")
        else:
            chosen_connector = selected
    else:
        st.text_input("Connector", value=str(chosen_connector), disabled=True, key=f"{prefix}_connector_fixed")

    if not chosen_connector:
        st.info("Select a connector to continue.")
        return []

    connector_base = connector_base_name(chosen_connector)
    pool_type = connector_pool_type(chosen_connector)
    if not pool_type:
        pool_type = st.selectbox("Pool Type", ["clmm", "amm"], key=f"{prefix}_pool_type")
    else:
        st.caption(f"Pool Type: {pool_type}")

    networks = _get_gateway_networks(prefix)
    network_ids = sorted({item.get("network_id") for item in networks if item.get("network_id")})
    network_options = ["(select network)"] + network_ids
    selected_network_id = st.selectbox(
        "Gateway Network",
        options=network_options,
        index=0,
        key=f"{prefix}_network",
    )

    data_source = "Search Results"
    if existing_only:
        data_source = "Existing Pools"
    elif show_existing_toggle:
        data_source = st.radio(
            "Data Source",
            ["Search Results", "Existing Pools"],
            horizontal=True,
            key=f"{prefix}_data_source",
        )

    if selected_network_id == "(select network)":
        st.info("Select a network before searching pools.")
        return []

    token_a = None
    token_b = None
    search_term = None
    pools_results: List[Dict] = []
    if data_source == "Search Results":
        st.markdown("**Search Pools**")
        cols = st.columns(2)
        with cols[0]:
            token_a = st.text_input("Token A (symbol or address)", key=f"{prefix}_token_a")
        with cols[1]:
            token_b = st.text_input("Token B (symbol or address)", key=f"{prefix}_token_b")
        search_term = st.text_input("Search (optional)", key=f"{prefix}_search_term")
        with st.expander("Advanced Search", expanded=False):
            pages = st.number_input("Pages", min_value=1, max_value=10, value=1, step=1, key=f"{prefix}_pages")
            limit = st.number_input("Limit", min_value=1, max_value=200, value=50, step=1, key=f"{prefix}_limit")

        if st.button("Search Pools", key=f"{prefix}_search", use_container_width=True):
            _maybe_add_token(prefix, selected_network_id, token_a or "")
            _maybe_add_token(prefix, selected_network_id, token_b or "")

            token_a_value = _resolve_token_for_search(prefix, selected_network_id, token_a or "")
            token_b_value = _resolve_token_for_search(prefix, selected_network_id, token_b or "")
            with st.spinner("Searching pools..."):
                response = backend_api_request(
                    "GET",
                    "/metadata/pools",
                    params={
                        "connector": connector_base,
                        "network_id": selected_network_id,
                        "pool_type": pool_type,
                        "token_a": token_a_value or None,
                        "token_b": token_b_value or None,
                        "search": search_term or None,
                        "pages": int(pages),
                        "limit": int(limit),
                    },
                )
            if response.get("ok"):
                payload = response.get("data", {})
                pools_results = payload.get("pools", [])
                st.session_state[f"{prefix}_search_results"] = pools_results
            else:
                st.error(_format_api_error(response, "Failed to fetch pools."))
        else:
            pools_results = st.session_state.get(f"{prefix}_search_results", [])
    else:
        st.markdown("**Existing Pools**")
        existing_search = st.text_input("Filter (symbol or address)", key=f"{prefix}_existing_search")
        if st.button("Load Pools", key=f"{prefix}_existing_load", use_container_width=True):
            network_value = extract_network_value(selected_network_id or "")
            pools_results = _get_gateway_pools(
                prefix,
                connector_base,
                network_value or "",
                pool_type,
                existing_search or "",
                force_refresh=True,
            )
            st.session_state[f"{prefix}_existing_results"] = pools_results
        else:
            pools_results = st.session_state.get(f"{prefix}_existing_results", [])

    normalized_pools = []
    if pools_results:
        if data_source == "Search Results":
            normalized_pools = [normalize_search_pool(pool) for pool in pools_results if isinstance(pool, dict)]
        else:
            normalized_pools = [normalize_existing_pool(pool) for pool in pools_results if isinstance(pool, dict)]

    filtered_pools = normalized_pools
    if show_filters and data_source == "Search Results":
        st.markdown("**Filters & Sorting**")
        filter_cols = st.columns(4)
        with filter_cols[0]:
            min_tvl = st.number_input("Min TVL (USD)", min_value=0.0, step=1000.0, value=0.0, key=f"{prefix}_min_tvl")
        with filter_cols[1]:
            min_volume = st.number_input(
                "Min Volume 24h (USD)",
                min_value=0.0,
                step=1000.0,
                value=0.0,
                key=f"{prefix}_min_volume",
            )
        with filter_cols[2]:
            min_apy = st.number_input("Min APY %", min_value=0.0, step=1.0, value=0.0, key=f"{prefix}_min_apy")
        with filter_cols[3]:
            sort_choice = st.selectbox(
                "Sort By",
                ["Volume 24h (desc)", "TVL (desc)", "APY (desc)"],
                key=f"{prefix}_sort",
            )

        def _to_float(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        filtered = []
        for pool in filtered_pools:
            tvl_value = _to_float(pool.get("tvl_usd"))
            volume_value = _to_float(pool.get("volume_24h"))
            apy_value = _to_float(pool.get("apy"))
            if min_tvl > 0 and (tvl_value is None or tvl_value < min_tvl):
                continue
            if min_volume > 0 and (volume_value is None or volume_value < min_volume):
                continue
            if min_apy > 0 and (apy_value is None or apy_value < min_apy):
                continue
            filtered.append(pool)
        filtered_pools = filtered

        sort_field = {
            "Volume 24h (desc)": "volume_24h",
            "TVL (desc)": "tvl_usd",
            "APY (desc)": "apy",
        }.get(sort_choice, "volume_24h")

        def sort_key(pool):
            value = _to_float(pool.get(sort_field))
            return value if value is not None else float("-inf")

        filtered_pools = sorted(filtered_pools, key=sort_key, reverse=True)

    if not filtered_pools:
        if data_source == "Search Results" and st.session_state.get(f"{prefix}_search_results") is not None:
            st.info("No pools found.")
        return []

    st.caption(f"{len(filtered_pools)} pools available.")
    display_rows = []
    for pool in filtered_pools:
        pair = pool.get("trading_pair") or "-"
        if data_source == "Search Results":
            display_rows.append({
                "Pair": pair,
                "Fee %": pool.get("fee_tier"),
                "Volume 24h": pool.get("volume_24h"),
                "TVL (USD)": pool.get("tvl_usd"),
                "APY %": pool.get("apy"),
                "Address": pool.get("address"),
            })
        else:
            display_rows.append({
                "Pair": pair,
                "Fee %": pool.get("fee_tier"),
                "Base Address": pool.get("base_address"),
                "Quote Address": pool.get("quote_address"),
                "Address": pool.get("address"),
            })
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

    options = {}
    for pool in filtered_pools:
        address = pool.get("address", "")
        pair = pool.get("trading_pair") or "unknown"
        label = " | ".join(part for part in [pair, address] if part)
        options[label] = pool

    selected_label = st.selectbox(
        "Select Pool",
        list(options.keys()),
        key=f"{prefix}_pool_choice",
    )
    selected_pool = options.get(selected_label, {})

    st.markdown(f"**Selected Pool**: {selected_pool.get('trading_pair') or 'unknown'}")
    if st.button("Use Selected Pool", key=f"{prefix}_pool_apply"):
        added = _maybe_add_pool(
            prefix=prefix,
            connector_name=connector_base,
            network_id=selected_network_id,
            pool_type=pool_type,
            pool=selected_pool,
        )
        if added and target_rows_key:
            current_rows = st.session_state.get(target_rows_key, [])
            row = generator.pool_to_override_row(selected_pool)
            st.session_state[target_rows_key] = generator.merge_override_rows(current_rows, [row])
    return st.session_state.get(target_rows_key, []) if target_rows_key else []
