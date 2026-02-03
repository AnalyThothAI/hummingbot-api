import re
import time

import pandas as pd
import streamlit as st
from eth_utils import is_address, is_checksum_address, to_checksum_address

from CONFIG import GATEWAY_ENABLED
from frontend.components.gateway_registry import render_gateway_pool_picker
from frontend.st_utils import backend_api_request, initialize_st_page

initialize_st_page(icon="🔗", show_readme=False)

st.title("Gateway")

if not GATEWAY_ENABLED:
    st.info("Gateway support is disabled. Set GATEWAY_ENABLED=true to enable this page.")
    st.stop()

status_response = backend_api_request("GET", "/gateway/status")
if not status_response.get("ok"):
    status_code = status_response.get("status_code")
    if status_code == 401:
        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
    else:
        st.error("Gateway status unavailable. Verify the backend API and Docker connectivity.")
    st.stop()

status_data = status_response.get("data", {})
container_running = status_data.get("running", False)

connectors_response = backend_api_request("GET", "/gateway/connectors")
wallets_response = backend_api_request("GET", "/accounts/gateway/wallets")
chains_response = backend_api_request("GET", "/gateway/chains")
networks_response = backend_api_request("GET", "/gateway/networks")

api_online = connectors_response.get("ok")


def build_connector_rows_and_meta(connectors_payload):
    rows = []
    meta = {}

    if isinstance(connectors_payload, dict):
        connectors_value = connectors_payload.get("connectors", connectors_payload)
    else:
        connectors_value = connectors_payload

    def collect_meta(name, details):
        if not name:
            return
        chain = None
        networks = []
        trading_types = []
        if isinstance(details, dict):
            chain = details.get("chain") or details.get("chain_type") or details.get("chainName")
            raw_networks = details.get("networks") or details.get("network") or []
            if isinstance(raw_networks, list):
                networks = [str(item) for item in raw_networks]
            elif raw_networks:
                networks = [str(raw_networks)]
            raw_trading = details.get("trading_types") or details.get("trading_type") or []
            if isinstance(raw_trading, list):
                trading_types = [str(item) for item in raw_trading]
            elif raw_trading:
                trading_types = [str(raw_trading)]

        meta[name] = {
            "chain": chain,
            "networks": networks,
            "trading_types": trading_types,
        }

    if isinstance(connectors_value, dict):
        for name, details in connectors_value.items():
            row = {"connector": name}
            if isinstance(details, dict):
                row["chain"] = details.get("chain") or details.get("chain_type") or details.get("chainName")
                networks = details.get("networks") or details.get("network")
                if isinstance(networks, list):
                    row["networks"] = ", ".join(str(item) for item in networks)
                elif networks:
                    row["networks"] = str(networks)
                trading_types = details.get("trading_types") or details.get("trading_type")
                if isinstance(trading_types, list):
                    row["trading_types"] = ", ".join(str(item) for item in trading_types)
                elif trading_types:
                    row["trading_types"] = str(trading_types)
            collect_meta(name, details)
            rows.append(row)
    elif isinstance(connectors_value, list):
        for item in connectors_value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("connector") or item.get("connector_name")
                if name:
                    row = {"connector": name}
                    row["chain"] = item.get("chain") or item.get("chain_type") or item.get("chainName")
                    networks = item.get("networks") or item.get("network")
                    if isinstance(networks, list):
                        row["networks"] = ", ".join(str(val) for val in networks)
                    elif networks:
                        row["networks"] = str(networks)
                    trading_types = item.get("trading_types") or item.get("trading_type")
                    if isinstance(trading_types, list):
                        row["trading_types"] = ", ".join(str(val) for val in trading_types)
                    elif trading_types:
                        row["trading_types"] = str(trading_types)
                    rows.append(row)
                    collect_meta(name, item)
                else:
                    rows.append({"connector": str(item)})
            else:
                rows.append({"connector": str(item)})

    return rows, meta


def parse_chain_options(chains_payload):
    chains = []
    if isinstance(chains_payload, list):
        for item in chains_payload:
            if isinstance(item, dict):
                chain = item.get("chain")
                if chain:
                    chains.append(chain)
    elif isinstance(chains_payload, dict):
        chains_value = chains_payload.get("chains", chains_payload)
        if isinstance(chains_value, dict):
            chains = list(chains_value.keys())
        elif isinstance(chains_value, list):
            for item in chains_value:
                if isinstance(item, dict):
                    chain = item.get("chain")
                    if chain:
                        chains.append(chain)
    return sorted({str(chain) for chain in chains})


def parse_chain_defaults(chains_payload):
    defaults = {}
    if isinstance(chains_payload, dict):
        chains_value = chains_payload.get("chains", chains_payload)
    else:
        chains_value = chains_payload
    if isinstance(chains_value, list):
        for item in chains_value:
            if isinstance(item, dict):
                chain = item.get("chain")
                default_network = item.get("defaultNetwork") or item.get("default_network")
                if chain and default_network:
                    defaults[str(chain)] = str(default_network)
    elif isinstance(chains_value, dict):
        for chain, details in chains_value.items():
            if isinstance(details, dict):
                default_network = details.get("defaultNetwork") or details.get("default_network")
                if default_network:
                    defaults[str(chain)] = str(default_network)
    return defaults


def parse_network_options(networks_payload, chains_payload):
    network_ids = []
    if isinstance(networks_payload, dict):
        networks_value = networks_payload.get("networks", networks_payload)
        if isinstance(networks_value, list):
            for item in networks_value:
                if isinstance(item, dict):
                    network_id = item.get("network_id") or item.get("networkId")
                    if not network_id:
                        chain = item.get("chain")
                        network = item.get("network")
                        if chain and network:
                            network_id = f"{chain}-{network}"
                    if network_id:
                        network_ids.append(network_id)
                elif isinstance(item, str):
                    network_ids.append(item)
        elif isinstance(networks_value, dict):
            for chain, networks in networks_value.items():
                if isinstance(networks, list):
                    for network in networks:
                        network_ids.append(f"{chain}-{network}")
    elif isinstance(networks_payload, list):
        for item in networks_payload:
            if isinstance(item, dict):
                network_id = item.get("network_id") or item.get("networkId")
                if network_id:
                    network_ids.append(network_id)
            elif isinstance(item, str):
                network_ids.append(item)

    if not network_ids:
        if isinstance(chains_payload, dict):
            chains_value = chains_payload.get("chains", chains_payload)
        else:
            chains_value = chains_payload
        if isinstance(chains_value, list):
            for item in chains_value:
                if isinstance(item, dict):
                    chain = item.get("chain")
                    networks = item.get("networks", [])
                    if chain and isinstance(networks, list):
                        for network in networks:
                            network_ids.append(f"{chain}-{network}")

    return sorted({str(item) for item in network_ids if item})


def select_connector(label, key_prefix, connector_options):
    def normalize_value(value):
        if isinstance(value, dict):
            for key in ("name", "connector", "connector_name"):
                if value.get(key):
                    return str(value.get(key))
            return str(value)
        return str(value)

    if connector_options:
        choices = connector_options + ["(custom)"]
        selection = st.selectbox(label, choices, key=key_prefix)
        if selection == "(custom)":
            return st.text_input("Connector Name", key=f"{key_prefix}_custom")
        return normalize_value(selection)
    return st.text_input("Connector Name", key=f"{key_prefix}_text")


def select_network(label, key_prefix, network_options):
    if network_options:
        choices = network_options + ["(custom)"]
        selection = st.selectbox(label, choices, key=key_prefix)
        if selection == "(custom)":
            return st.text_input("Network ID (chain-network)", key=f"{key_prefix}_custom")
        return selection
    return st.text_input("Network ID (chain-network)", key=f"{key_prefix}_text")


def split_network_id(network_id: str):
    if not network_id:
        return "", ""
    if "-" in network_id:
        chain, network = network_id.split("-", 1)
        return chain, network
    return network_id, ""


def normalize_evm_address(address: str):
    if not is_address(address):
        return None, "Invalid EVM address."
    checksum = to_checksum_address(address)
    if not is_checksum_address(address):
        return checksum, f"Checksum address applied: {checksum}"
    return checksum, None


def is_valid_solana_address(address: str) -> bool:
    return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address))


def to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_api_error(response, fallback: str):
    data = response.get("data", {})
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return detail
    error = response.get("error")
    return error or fallback


def apply_pending_widget_updates(state_key: str):
    pending = st.session_state.pop(state_key, None)
    if isinstance(pending, dict):
        for key, value in pending.items():
            if value is not None:
                st.session_state[key] = value


def resolve_token_address(network_id: str, symbol: str) -> str | None:
    if not network_id or not symbol:
        return None
    symbol_value = symbol.strip()
    if not symbol_value:
        return None
    if symbol_value.startswith("0x") or is_valid_solana_address(symbol_value):
        return None

    cache = st.session_state.setdefault("token_symbol_cache", {})
    cache_key = f"{network_id}:{symbol_value.lower()}"
    if cache_key in cache:
        return cache.get(cache_key) or None

    response = backend_api_request(
        "GET",
        f"/gateway/networks/{network_id}/tokens",
        params={"search": symbol_value},
    )
    if response.get("ok"):
        payload = response.get("data", {})
        tokens = payload.get("tokens", [])
        if isinstance(tokens, list):
            for token in tokens:
                if not isinstance(token, dict):
                    continue
                token_symbol = token.get("symbol")
                if token_symbol and token_symbol.lower() == symbol_value.lower():
                    address = token.get("address")
                    cache[cache_key] = address or ""
                    return address
        cache[cache_key] = ""
        return None

    return None


def auto_fill_token_address(symbol_key: str, address_key: str, network_id: str, auto_key: str):
    if not network_id:
        return

    symbol_value = st.session_state.get(symbol_key)
    if not symbol_value or not symbol_value.strip():
        return

    address_value = st.session_state.get(address_key)
    auto_value_key = f"{auto_key}_auto_value"
    resolved_key_key = f"{auto_key}_resolved_key"

    if address_value and st.session_state.get(auto_value_key) is None and st.session_state.get(resolved_key_key) is None:
        return

    if address_value and st.session_state.get(auto_value_key) and address_value != st.session_state.get(auto_value_key):
        st.session_state[auto_value_key] = None
        st.session_state[resolved_key_key] = None

    lookup_key = f"{network_id}:{symbol_value.strip().lower()}"
    if address_value and st.session_state.get(resolved_key_key) == lookup_key:
        return

    last_change_key = f"{auto_key}_last_change"
    last_symbol_key = f"{auto_key}_last_symbol"
    last_network_key = f"{auto_key}_last_network"
    now = time.time()
    if st.session_state.get(last_symbol_key) != symbol_value or st.session_state.get(last_network_key) != network_id:
        st.session_state[last_symbol_key] = symbol_value
        st.session_state[last_network_key] = network_id
        st.session_state[last_change_key] = now

    elapsed = now - st.session_state.get(last_change_key, now)
    if elapsed < 0.5:
        time.sleep(0.5 - elapsed)

    if address_value and st.session_state.get(auto_value_key) and address_value != st.session_state.get(auto_value_key):
        return

    resolved_address = resolve_token_address(network_id, symbol_value)
    if resolved_address:
        st.session_state[address_key] = resolved_address
        st.session_state[auto_value_key] = resolved_address
        st.session_state[resolved_key_key] = lookup_key


def networks_for_connector(connector_name: str, fallback: list[str]):
    meta = connectors_meta.get(connector_name) if connector_name else None
    if not meta:
        return fallback
    chain = meta.get("chain")
    networks = meta.get("networks") or []
    if chain and networks:
        return sorted({f"{chain}-{network}" for network in networks})
    return fallback


def default_network_id_for_connector(
    connector_name: str,
    network_options: list[str],
    chain_defaults: dict[str, str],
):
    if not connector_name:
        return None
    meta = connectors_meta.get(connector_name)
    if not meta:
        return None
    chain = meta.get("chain")
    if not chain:
        return None
    default_network = chain_defaults.get(chain)
    if default_network:
        candidate = f"{chain}-{default_network}"
        if candidate in network_options:
            return candidate
    candidates = [opt for opt in network_options if opt.startswith(f"{chain}-")] or list(network_options)
    for suffix in ("mainnet", "mainnet-beta"):
        for option in candidates:
            if option.endswith(f"-{suffix}"):
                return option
    return candidates[0] if candidates else None


def set_default_selection(state_key: str, context_key: str, context_value: str, default_value: str | None):
    if not default_value:
        return
    if st.session_state.get(context_key) != context_value:
        st.session_state[context_key] = context_value
        st.session_state[state_key] = default_value


def set_default_choice(state_key: str, default_value: str, options: list[str]):
    if default_value in options and state_key not in st.session_state:
        st.session_state[state_key] = default_value


def preferred_pool_network(connector_name: str, network_options: list[str]):
    if connector_name == "uniswap" and "ethereum-bsc" in network_options:
        return "ethereum-bsc"
    return None




connectors_payload = connectors_response.get("data", {}) if connectors_response.get("ok") else {}
connectors_rows, connectors_meta = build_connector_rows_and_meta(connectors_payload)
connectors_list = sorted({str(name) for name in connectors_meta.keys() if name})

chains_payload = chains_response.get("data", {}) if chains_response.get("ok") else {}
chain_options = parse_chain_options(chains_payload)
chain_defaults = parse_chain_defaults(chains_payload)

networks_payload = networks_response.get("data", {}) if networks_response.get("ok") else {}
network_options = parse_network_options(networks_payload, chains_payload)

wallets_payload = wallets_response.get("data", []) if wallets_response.get("ok") else []
wallet_count = len(wallets_payload) if isinstance(wallets_payload, list) else 0

status_cols = st.columns(4)
with status_cols[0]:
    st.metric("Container", "Running" if container_running else "Stopped")
with status_cols[1]:
    st.metric("API", "Online" if api_online else "Unavailable")
with status_cols[2]:
    st.metric("Wallets", wallet_count if wallets_response.get("ok") else "Unavailable")
with status_cols[3]:
    st.metric("Connectors", len(connectors_list) if connectors_response.get("ok") else "Unavailable")

action_cols = st.columns([1, 2, 2])
with action_cols[0]:
    st.caption("Restart can take up to 60 seconds.")
    if st.button("Restart Gateway", type="primary", use_container_width=True):
        with st.spinner("Restarting Gateway..."):
            restart_response = backend_api_request("POST", "/gateway/restart", timeout=60)

        if restart_response.get("ok"):
            st.success("Gateway restart requested.")
        else:
            status_code = restart_response.get("status_code")
            error_text = str(restart_response.get("error", ""))
            if status_code == 401:
                st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
            elif status_code is None and "timed out" in error_text.lower():
                st.info("Restart request timed out. Gateway may still be restarting.")
            else:
                st.error(format_api_error(restart_response, "Failed to restart Gateway. Check API connectivity and logs."))

        status_check = backend_api_request("GET", "/gateway/status")
        if status_check.get("ok"):
            running_now = status_check.get("data", {}).get("running")
            if running_now:
                st.success("Gateway is running.")
            else:
                st.warning("Gateway is still restarting.")

if not container_running:
    st.warning("Gateway container is not running. Start Gateway before deploying bots.")

if not api_online and container_running:
    st.warning("Gateway API is not responding. Check the Gateway logs and network connectivity.")

tabs = st.tabs(["Tokens", "Pools", "Wallets", "Connectors"])

with tabs[0]:
    st.subheader("Tokens")
    st.caption("Add custom tokens and browse the token registry by network. Restart Gateway after adding tokens.")

    st.markdown("**Add Token**")
    st.caption("EVM chains require checksum addresses (mixed-case).")
    apply_pending_widget_updates("token_pending_updates")
    autofill_message = st.session_state.pop("token_autofill_message", None)
    if autofill_message:
        st.success(autofill_message)
    autofill_warnings = st.session_state.pop("token_autofill_warnings", None)
    if autofill_warnings:
        st.info(f"Metadata warnings: {', '.join(autofill_warnings)}")
    checksum_notice = st.session_state.pop("token_checksum_notice", None)
    if checksum_notice:
        st.info(checksum_notice)
    network_id = select_network("Network", "token_network", network_options)
    token_address = st.text_input("Token Address", key="token_address")
    symbol = st.text_input("Symbol", key="token_symbol")
    name = st.text_input("Name (optional)", key="token_name")
    decimals_value = st.session_state.get("token_decimals", 6)
    decimals = st.number_input("Decimals", min_value=0, max_value=36, step=1, value=decimals_value, key="token_decimals")

    lookup_address = None
    pending_updates = {}
    checksum_notice = None
    if network_id and "-" in network_id and token_address:
        chain, _ = split_network_id(network_id)
        if token_address.startswith("0x") and len(token_address) == 42:
            checksum_address, checksum_notice = normalize_evm_address(token_address)
            if checksum_address is None:
                st.error("Invalid EVM address. Check the address format.")
            else:
                lookup_address = checksum_address
                if checksum_address != token_address:
                    pending_updates["token_address"] = checksum_address
        elif chain == "solana" and is_valid_solana_address(token_address):
            lookup_address = token_address

    lookup_key = f"{network_id}:{lookup_address}" if lookup_address else None
    if lookup_key and st.session_state.get("token_lookup_key") != lookup_key:
        st.session_state["token_lookup_key"] = lookup_key
        response = backend_api_request(
            "GET",
            "/metadata/token",
            params={
                "network_id": network_id,
                "address": lookup_address,
            },
        )
        if response.get("ok"):
            payload = response.get("data", {})
            token = payload.get("token", {})
            pending_updates.update({
                "token_symbol": token.get("symbol"),
                "token_name": token.get("name"),
                "token_decimals": token.get("decimals"),
            })
            st.session_state["token_pending_updates"] = pending_updates
            st.session_state["token_autofill_message"] = "Token metadata loaded."
            warnings = payload.get("warnings", [])
            if warnings:
                st.session_state["token_autofill_warnings"] = warnings
            if checksum_notice:
                st.session_state["token_checksum_notice"] = checksum_notice
            st.rerun()
        else:
            status_code = response.get("status_code")
            if status_code == 401:
                st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
            else:
                st.error(format_api_error(response, "Failed to fetch token metadata."))

    submit_add_token = st.button("Add Token", key="token_add_submit")
    if submit_add_token:
        if not network_id or not token_address or not symbol:
            st.error("Network, token address, and symbol are required.")
        else:
            checksum_notice = None
            if token_address.startswith("0x"):
                checksum_address, checksum_notice = normalize_evm_address(token_address)
                if checksum_address is None:
                    st.error("Invalid EVM address. Check the address format.")
                else:
                    token_address = checksum_address

            if token_address:
                payload = {
                    "address": token_address,
                    "symbol": symbol,
                    "decimals": int(decimals),
                }
                if name:
                    payload["name"] = name
                response = backend_api_request(
                    "POST",
                    f"/gateway/networks/{network_id}/tokens",
                    json_body=payload,
                )
                if response.get("ok"):
                    message = response.get("data", {}).get(
                        "message",
                        "Token added. Restart Gateway for changes to take effect.",
                    )
                    st.success(message)
                    if checksum_notice:
                        st.info(checksum_notice)
                else:
                    status_code = response.get("status_code")
                    if status_code == 401:
                        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                    else:
                        st.error(format_api_error(response, "Failed to add token."))

    st.divider()
    st.markdown("**Tokens by Network**")
    st.caption("Select a network to view tokens. No search required.")
    lookup_network = select_network("Network", "token_list_network", network_options)

    if lookup_network:
        response = backend_api_request(
            "GET",
            f"/gateway/networks/{lookup_network}/tokens",
        )
        if response.get("ok"):
            payload = response.get("data", {})
            tokens = payload.get("tokens", payload if isinstance(payload, list) else [])
            if tokens:
                chain, network = split_network_id(lookup_network)
                token_rows = []
                for token in tokens:
                    if isinstance(token, dict):
                        row = dict(token)
                        row["chain"] = chain
                        row["network"] = network
                        row["network_id"] = lookup_network
                        token_rows.append(row)
                st.dataframe(pd.DataFrame(token_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No tokens found for this network.")
        else:
            status_code = response.get("status_code")
            if status_code == 401:
                st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
            else:
                st.error(format_api_error(response, "Failed to fetch tokens."))

with tabs[1]:
    st.subheader("Pools")
    st.caption("Add custom pools for supported connectors. Restart Gateway after adding pools.")

    st.markdown("**Pool Finder**")
    render_gateway_pool_picker(
        prefix="gw_pool",
        connector_name=None,
        allow_connector_override=True,
        target_rows_key=None,
        show_existing_toggle=True,
        show_filters=True,
    )

    connector_name = st.session_state.get("gw_pool_connector")
    if connector_name == "(custom)":
        connector_name = st.session_state.get("gw_pool_connector_custom")
    network_id = st.session_state.get("gw_pool_network")
    if network_id == "(select network)":
        network_id = None
    pool_type = st.session_state.get("gw_pool_pool_type")

    st.divider()
    with st.expander("Advanced: Manual Add Pool", expanded=False):
        st.caption("Use this only when Search cannot resolve a pool (e.g., missing Gecko mapping).")
        st.caption("Auto-fill uses Gateway token list for the selected network. Clear address to re-run.")

        col1a, col2a, col3a = st.columns(3)
        with col1a:
            base_symbol = st.text_input("Base Symbol", key="pool_base_symbol")
        with col2a:
            quote_symbol = st.text_input("Quote Symbol", key="pool_quote_symbol")
        with col3a:
            fee_pct_value = st.session_state.get("pool_fee_pct", 0.0)
            fee_pct = st.number_input(
                "Fee Pct (optional)",
                min_value=0.0,
                step=0.01,
                format="%.4f",
                value=fee_pct_value,
                key="pool_fee_pct",
            )

        auto_fill_token_address("pool_base_symbol", "pool_base_address", network_id, "pool_base")
        auto_fill_token_address("pool_quote_symbol", "pool_quote_address", network_id, "pool_quote")

        col1b, col2b = st.columns(2)
        with col1b:
            base_address = st.text_input("Base Token Address", key="pool_base_address")
        with col2b:
            quote_address = st.text_input("Quote Token Address", key="pool_quote_address")

        pool_address = st.text_input("Pool Address", key="pool_address")
        submit_add_pool = st.button("Add Pool", key="pool_add_submit")

        if submit_add_pool:
            if not connector_name or not network_id or not pool_type:
                st.error("Connector, network, and pool type are required.")
            elif not base_symbol or not quote_symbol:
                st.error("Base and quote symbols are required.")
            elif not base_address or not quote_address or not pool_address:
                st.error("Token addresses and pool address are required.")
            else:
                if "-" in network_id:
                    _, network_value = network_id.split("-", 1)
                else:
                    network_value = network_id

                payload = {
                    "connector_name": connector_name,
                    "type": pool_type,
                    "network": network_value,
                    "address": pool_address,
                    "base": base_symbol,
                    "quote": quote_symbol,
                    "base_address": base_address,
                    "quote_address": quote_address,
                }
                if fee_pct and fee_pct > 0:
                    payload["fee_pct"] = float(fee_pct)

                response = backend_api_request("POST", "/gateway/pools", json_body=payload)
                if response.get("ok"):
                    message = response.get("data", {}).get("message", "Pool added.")
                    st.success(message)
                else:
                    status_code = response.get("status_code")
                    if status_code == 401:
                        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                    else:
                        st.error(format_api_error(response, "Failed to add pool."))

with tabs[2]:
    st.subheader("Wallets")
    st.caption("Gateway encrypts and stores private keys securely. Restart not required for wallet changes.")

    if wallets_response.get("ok"):
        if isinstance(wallets_payload, list) and wallets_payload:
            st.dataframe(pd.DataFrame(wallets_payload), use_container_width=True, hide_index=True)
        else:
            st.info("No wallets configured. Add a wallet to enable live trading.")
    else:
        st.error("Failed to fetch Gateway wallets.")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Add Wallet**")
        with st.form("gateway_add_wallet"):
            if chain_options:
                chain = st.selectbox("Chain", chain_options)
            else:
                chain = st.text_input("Chain (e.g., solana, ethereum)")
            private_key = st.text_input("Private Key", type="password")
            submitted = st.form_submit_button("Add Wallet")

            if submitted:
                if not chain or not private_key:
                    st.error("Chain and private key are required.")
                else:
                    response = backend_api_request(
                        "POST",
                        "/accounts/gateway/add-wallet",
                        json_body={"chain": chain, "private_key": private_key},
                    )
                    if response.get("ok"):
                        st.success("Wallet added.")
                        st.rerun()
                    else:
                        status_code = response.get("status_code")
                        if status_code == 401:
                            st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                        else:
                            st.error(response.get("error", "Failed to add wallet."))

    with col2:
        st.markdown("**Remove Wallet**")
        wallet_options = {}
        if isinstance(wallets_payload, list):
            for wallet in wallets_payload:
                chain = wallet.get("chain", "unknown")
                address = wallet.get("address", "")
                is_default = wallet.get("isDefault", False)
                if address:
                    label = f"{chain} | {address}{' (default)' if is_default else ''}"
                    wallet_options[label] = (chain, address)

        if wallet_options:
            with st.form("gateway_remove_wallet"):
                selected_label = st.selectbox("Wallet", list(wallet_options.keys()))
                submitted = st.form_submit_button("Remove Wallet")
                if submitted:
                    chain, address = wallet_options[selected_label]
                    response = backend_api_request("DELETE", f"/accounts/gateway/{chain}/{address}")
                    if response.get("ok"):
                        st.success("Wallet removed.")
                        st.rerun()
                    else:
                        status_code = response.get("status_code")
                        if status_code == 401:
                            st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                        else:
                            st.error(response.get("error", "Failed to remove wallet."))
        else:
            st.info("No wallets to remove.")

with tabs[3]:
    st.subheader("Connectors")
    if connectors_response.get("ok"):
        if connectors_rows:
            st.dataframe(pd.DataFrame(connectors_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No connectors reported by Gateway.")
    else:
        st.error("Failed to fetch connectors from Gateway API.")
