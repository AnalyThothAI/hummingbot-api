from decimal import Decimal
from typing import Optional

import pandas as pd
import streamlit as st
from eth_utils import is_address, is_checksum_address, to_checksum_address

from CONFIG import GATEWAY_ENABLED
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


def parse_token_list(tokens_raw: str) -> list[str]:
    if not tokens_raw:
        return []
    return [token.strip() for token in tokens_raw.replace("\n", ",").split(",") if token.strip()]


def build_wallet_options(wallets_payload: list, chain: Optional[str] = None) -> dict[str, str]:
    options = {}
    if isinstance(wallets_payload, list):
        for wallet in wallets_payload:
            wallet_chain = wallet.get("chain", "unknown")
            if chain and wallet_chain != chain:
                continue
            address = wallet.get("address", "")
            is_default = wallet.get("isDefault", False)
            if address:
                label = f"{wallet_chain} | {address}{' (default)' if is_default else ''}"
                options[label] = address
    return options


def format_api_error(response, fallback: str):
    data = response.get("data", {})
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return detail
    error = response.get("error")
    return error or fallback


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


def preferred_spender_option(connector_name: str, trading_types: list[str]) -> str | None:
    if not connector_name:
        return None
    if trading_types:
        if "router" in trading_types:
            return f"{connector_name}/router"
        return f"{connector_name}/{trading_types[0]}"
    return connector_name


def build_spender_options(connector_name: str, trading_types: list[str]) -> list[str]:
    options = []
    if connector_name:
        if trading_types:
            options.extend([f"{connector_name}/{value}" for value in trading_types])
        else:
            options.append(connector_name)
    options.append("(custom)")
    return options


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

tabs = st.tabs(["Tokens", "Pools", "Wallets", "Allowances", "Connectors"])

with tabs[0]:
    st.subheader("Tokens")
    st.caption("Add custom tokens and browse the token registry by network. Restart Gateway after adding tokens.")

    st.markdown("**Add Token**")
    st.caption("EVM chains require checksum addresses (mixed-case).")
    with st.form("gateway_add_token"):
        network_id = select_network("Network", "token_network", network_options)
        token_address = st.text_input("Token Address")
        symbol = st.text_input("Symbol")
        name = st.text_input("Name (optional)")
        decimals = st.number_input("Decimals", min_value=0, max_value=36, step=1, value=6)
        submit_add_token = st.form_submit_button("Add Token")

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

    st.markdown("**Add Pool**")
    with st.form("gateway_add_pool"):
        set_default_choice("pool_connector", "uniswap", connectors_list)
        connector_name = select_connector("Connector", "pool_connector", connectors_list)
        pool_network_options = networks_for_connector(connector_name, network_options)
        preferred_network = preferred_pool_network(connector_name, pool_network_options)
        if preferred_network:
            set_default_selection("pool_network", "pool_network_ctx", connector_name, preferred_network)
        else:
            default_pool_network = default_network_id_for_connector(
                connector_name,
                pool_network_options,
                chain_defaults,
            )
            set_default_selection("pool_network", "pool_network_ctx", connector_name, default_pool_network)
        network_id = select_network("Network", "pool_network", pool_network_options)
        pool_type = st.selectbox("Pool Type", ["clmm", "amm"])

        col1a, col2a, col3a = st.columns(3)
        with col1a:
            base_symbol = st.text_input("Base Symbol")
        with col2a:
            quote_symbol = st.text_input("Quote Symbol")
        with col3a:
            fee_pct = st.number_input(
                "Fee Pct (optional)",
                min_value=0.0,
                step=0.01,
                format="%.4f",
                value=0.0,
            )

        col1b, col2b = st.columns(2)
        with col1b:
            base_address = st.text_input("Base Token Address")
        with col2b:
            quote_address = st.text_input("Quote Token Address")

        pool_address = st.text_input("Pool Address")
        submit_add_pool = st.form_submit_button("Add Pool")

        if submit_add_pool:
            if not connector_name or not network_id:
                st.error("Connector and network are required.")
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

    st.divider()
    st.markdown("**Pools by Network**")
    st.caption("Select a connector and network to view pools.")
    set_default_choice("pool_lookup_connector", "uniswap", connectors_list)
    browse_connector = select_connector("Connector", "pool_lookup_connector", connectors_list)
    browse_network_options = networks_for_connector(browse_connector, network_options)
    preferred_browse_network = preferred_pool_network(browse_connector, browse_network_options)
    if preferred_browse_network:
        set_default_selection("pool_lookup_network", "pool_lookup_network_ctx", browse_connector, preferred_browse_network)
    else:
        default_browse_network = default_network_id_for_connector(
            browse_connector,
            browse_network_options,
            chain_defaults,
        )
        set_default_selection("pool_lookup_network", "pool_lookup_network_ctx", browse_connector, default_browse_network)
    browse_network = select_network("Network", "pool_lookup_network", browse_network_options)

    if browse_connector and browse_network:
        if "-" in browse_network:
            _, network_value = browse_network.split("-", 1)
        else:
            network_value = browse_network
        response = backend_api_request(
            "GET",
            "/gateway/pools",
            params={"connector_name": browse_connector, "network": network_value},
        )
        if response.get("ok"):
            pools = response.get("data", [])
            if pools:
                st.dataframe(pd.DataFrame(pools), use_container_width=True, hide_index=True)
            else:
                st.info("No pools found for this connector/network.")
        else:
            status_code = response.get("status_code")
            if status_code == 401:
                st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
            else:
                st.error(format_api_error(response, "Failed to fetch pools."))
    else:
        st.info("Select a connector and network to load pools.")

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
    st.subheader("Allowances")
    st.caption("EVM connectors require ERC20 approvals for router contracts.")

    st.markdown("**Check Allowance**")
    with st.form("gateway_check_allowance"):
        allowance_connector = select_connector("Connector", "allowance_connector", connectors_list)
        allowance_trading_types = connectors_meta.get(allowance_connector, {}).get("trading_types", [])
        spender_options = build_spender_options(allowance_connector, allowance_trading_types)
        preferred_spender = preferred_spender_option(allowance_connector, allowance_trading_types)
        if preferred_spender:
            set_default_choice("allowance_spender_choice", preferred_spender, spender_options)
        allowance_spender_choice = st.selectbox("Spender", spender_options, key="allowance_spender_choice")
        allowance_network_options = networks_for_connector(allowance_connector, network_options)
        default_allowance_network = default_network_id_for_connector(
            allowance_connector,
            allowance_network_options,
            chain_defaults,
        )
        set_default_selection("allowance_network", "allowance_network_ctx", allowance_connector, default_allowance_network)
        allowance_network_id = select_network("Network", "allowance_network", allowance_network_options)

        chain_hint, _ = split_network_id(allowance_network_id)
        if chain_hint and chain_hint != "ethereum":
            st.info("Allowances apply to EVM networks only (chain = ethereum).")
        allowance_wallet_options = build_wallet_options(wallets_payload, chain_hint or None)
        if allowance_wallet_options:
            default_wallet_label = next(
                (label for label in allowance_wallet_options if "(default)" in label),
                None,
            )
            if default_wallet_label:
                set_default_choice("allowance_wallet", default_wallet_label, list(allowance_wallet_options.keys()))
            wallet_label = st.selectbox("Wallet", list(allowance_wallet_options.keys()), key="allowance_wallet")
            allowance_wallet_address = allowance_wallet_options.get(wallet_label)
        else:
            allowance_wallet_address = st.text_input("Wallet Address", key="allowance_wallet_text")

        if allowance_spender_choice == "(custom)":
            allowance_spender = st.text_input(
                "Custom Spender",
                key="allowance_spender_custom",
                placeholder="pancakeswap/router or 0x...",
            )
        else:
            allowance_spender = allowance_spender_choice
        tokens_raw = st.text_input("Tokens (comma-separated)", placeholder="USDT, USDC")
        submit_allowance = st.form_submit_button("Check Allowance")

        if submit_allowance:
            allowance_spender_clean = allowance_spender.strip()
            if not allowance_network_id or not allowance_spender_clean or not allowance_wallet_address:
                st.error("Network, spender, and wallet address are required.")
            else:
                checksum_notice = None
                if allowance_wallet_address.startswith("0x"):
                    checksum_address, checksum_notice = normalize_evm_address(allowance_wallet_address)
                    if checksum_address is None:
                        st.error("Invalid EVM address. Check the wallet format.")
                        allowance_wallet_address = None
                    else:
                        allowance_wallet_address = checksum_address

                tokens = parse_token_list(tokens_raw)
                if allowance_wallet_address and tokens:
                    payload = {
                        "network_id": allowance_network_id,
                        "address": allowance_wallet_address,
                        "tokens": tokens,
                        "spender": allowance_spender_clean,
                    }
                    with st.spinner("Fetching allowances..."):
                        response = backend_api_request("POST", "/gateway/allowances", json_body=payload, timeout=60)
                    if response.get("ok"):
                        data = response.get("data", {})
                        approvals = data.get("approvals", {})
                        rows = [{"token": token, "allowance": value} for token, value in approvals.items()]
                        if rows:
                            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                        else:
                            st.info("No allowances returned for the selected tokens.")
                        if checksum_notice:
                            st.info(checksum_notice)
                    else:
                        status_code = response.get("status_code")
                        if status_code == 401:
                            st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                        elif status_code is None and "timed out" in str(response.get("error", "")).lower():
                            st.error("Allowance request timed out. Check Gateway connectivity and try again.")
                        else:
                            st.error(format_api_error(response, "Failed to fetch allowances."))
                elif allowance_wallet_address and not tokens:
                    st.error("At least one token is required.")

    st.divider()
    st.markdown("**Approve Token**")
    st.caption("Leave amount blank to approve unlimited spending.")
    with st.form("gateway_approve_token"):
        approve_connector = select_connector("Connector", "approve_connector", connectors_list)
        approve_trading_types = connectors_meta.get(approve_connector, {}).get("trading_types", [])
        approve_spender_options = build_spender_options(approve_connector, approve_trading_types)
        preferred_spender = preferred_spender_option(approve_connector, approve_trading_types)
        if preferred_spender:
            set_default_choice("approve_spender_choice", preferred_spender, approve_spender_options)
        approve_spender_choice = st.selectbox("Spender", approve_spender_options, key="approve_spender_choice")
        approve_network_options = networks_for_connector(approve_connector, network_options)
        default_approve_network = default_network_id_for_connector(
            approve_connector,
            approve_network_options,
            chain_defaults,
        )
        set_default_selection("approve_network", "approve_network_ctx", approve_connector, default_approve_network)
        approve_network_id = select_network("Network", "approve_network", approve_network_options)

        chain_hint, _ = split_network_id(approve_network_id)
        if chain_hint and chain_hint != "ethereum":
            st.info("Approvals apply to EVM networks only (chain = ethereum).")
        approve_wallet_options = build_wallet_options(wallets_payload, chain_hint or None)
        if approve_wallet_options:
            default_wallet_label = next(
                (label for label in approve_wallet_options if "(default)" in label),
                None,
            )
            if default_wallet_label:
                set_default_choice("approve_wallet", default_wallet_label, list(approve_wallet_options.keys()))
            wallet_label = st.selectbox("Wallet", list(approve_wallet_options.keys()), key="approve_wallet")
            approve_wallet_address = approve_wallet_options.get(wallet_label)
        else:
            approve_wallet_address = st.text_input("Wallet Address", key="approve_wallet_text")

        if approve_spender_choice == "(custom)":
            approve_spender = st.text_input(
                "Custom Spender",
                key="approve_spender_custom",
                placeholder="pancakeswap/router or 0x...",
            )
        else:
            approve_spender = approve_spender_choice
        approve_token = st.text_input("Token Symbol or Address", placeholder="USDT")
        approve_amount_raw = st.text_input("Approve Amount (optional)", placeholder="Leave blank for unlimited")
        submit_approve = st.form_submit_button("Approve Token")

        if submit_approve:
            approve_spender_clean = approve_spender.strip()
            approve_token_clean = approve_token.strip()
            if not approve_network_id or not approve_spender_clean or not approve_wallet_address or not approve_token_clean:
                st.error("Network, spender, wallet address, and token are required.")
            else:
                checksum_notice = None
                if approve_wallet_address.startswith("0x"):
                    checksum_address, checksum_notice = normalize_evm_address(approve_wallet_address)
                    if checksum_address is None:
                        st.error("Invalid EVM address. Check the wallet format.")
                        approve_wallet_address = None
                    else:
                        approve_wallet_address = checksum_address

                amount_value = None
                if approve_amount_raw:
                    try:
                        Decimal(approve_amount_raw)
                        amount_value = approve_amount_raw
                    except Exception:
                        st.error("Approve amount must be a numeric value.")

                if approve_wallet_address and (approve_amount_raw == "" or amount_value is not None):
                    payload = {
                        "network_id": approve_network_id,
                        "address": approve_wallet_address,
                        "token": approve_token_clean,
                        "spender": approve_spender_clean,
                    }
                    if amount_value is not None:
                        payload["amount"] = amount_value
                    with st.spinner("Submitting approval..."):
                        response = backend_api_request("POST", "/gateway/approve", json_body=payload, timeout=60)
                    if response.get("ok"):
                        data = response.get("data", {})
                        tx_hash = data.get("signature") or data.get("txHash")
                        message = "Approval submitted."
                        if tx_hash:
                            message = f"Approval submitted: {tx_hash}"
                        st.success(message)
                        if checksum_notice:
                            st.info(checksum_notice)
                    else:
                        status_code = response.get("status_code")
                        if status_code == 401:
                            st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                        elif status_code is None and "timed out" in str(response.get("error", "")).lower():
                            st.error("Approve request timed out. Check Gateway connectivity and try again.")
                        else:
                            st.error(format_api_error(response, "Failed to approve token."))

with tabs[4]:
    st.subheader("Connectors")
    if connectors_response.get("ok"):
        if connectors_rows:
            st.dataframe(pd.DataFrame(connectors_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No connectors reported by Gateway.")
    else:
        st.error("Failed to fetch connectors from Gateway API.")
