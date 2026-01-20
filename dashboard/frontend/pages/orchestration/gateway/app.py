import pandas as pd
import streamlit as st

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


def build_connector_rows(connectors_payload):
    rows = []
    if isinstance(connectors_payload, dict):
        connectors_value = connectors_payload.get("connectors", connectors_payload)
    else:
        connectors_value = connectors_payload

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
            rows.append(row)
    elif isinstance(connectors_value, list):
        for item in connectors_value:
            rows.append({"connector": str(item)})

    return rows


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
    if connector_options:
        choices = connector_options + ["(custom)"]
        selection = st.selectbox(label, choices, key=key_prefix)
        if selection == "(custom)":
            return st.text_input("Connector Name", key=f"{key_prefix}_custom")
        return selection
    return st.text_input("Connector Name", key=f"{key_prefix}_text")


def select_network(label, key_prefix, network_options):
    if network_options:
        choices = network_options + ["(custom)"]
        selection = st.selectbox(label, choices, key=key_prefix)
        if selection == "(custom)":
            return st.text_input("Network ID (chain-network)", key=f"{key_prefix}_custom")
        return selection
    return st.text_input("Network ID (chain-network)", key=f"{key_prefix}_text")


connectors_payload = connectors_response.get("data", {}) if connectors_response.get("ok") else {}
connectors_rows = build_connector_rows(connectors_payload)
connectors_list = sorted({row.get("connector") for row in connectors_rows if row.get("connector")})

chains_payload = chains_response.get("data", {}) if chains_response.get("ok") else {}
chain_options = parse_chain_options(chains_payload)

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
    if st.button("Restart Gateway", type="primary", use_container_width=True):
        restart_response = backend_api_request("POST", "/gateway/restart")
        if restart_response.get("ok"):
            st.success("Gateway restart requested.")
        else:
            status_code = restart_response.get("status_code")
            if status_code == 401:
                st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
            else:
                st.error("Failed to restart Gateway. Check API connectivity and logs.")

if not container_running:
    st.warning("Gateway container is not running. Start Gateway before deploying bots.")

if not api_online and container_running:
    st.warning("Gateway API is not responding. Check the Gateway logs and network connectivity.")

tabs = st.tabs(["Wallets", "Tokens", "Pools", "Connectors"])

with tabs[0]:
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

with tabs[1]:
    st.subheader("Tokens")
    st.caption("Add custom tokens to Gateway. Restart Gateway after adding tokens.")

    with st.form("gateway_add_token"):
        network_id = select_network("Network", "token_network", network_options)
        token_address = st.text_input("Token Address")
        symbol = st.text_input("Symbol")
        name = st.text_input("Name (optional)")
        decimals = st.number_input("Decimals", min_value=0, max_value=36, step=1, value=6)
        submitted = st.form_submit_button("Add Token")

        if submitted:
            if not network_id or not token_address or not symbol:
                st.error("Network, token address, and symbol are required.")
            else:
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
                else:
                    status_code = response.get("status_code")
                    if status_code == 401:
                        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                    else:
                        st.error(response.get("error", "Failed to add token."))

    st.divider()
    st.markdown("**Lookup Tokens**")
    with st.form("gateway_token_lookup"):
        lookup_network = select_network("Network", "token_lookup_network", network_options)
        search = st.text_input("Search (symbol or name)", key="token_lookup_search")
        submitted = st.form_submit_button("Search Tokens")

    if submitted:
        if not lookup_network:
            st.error("Network is required.")
        else:
            response = backend_api_request(
                "GET",
                f"/gateway/networks/{lookup_network}/tokens",
                params={"search": search} if search else None,
            )
            if response.get("ok"):
                payload = response.get("data", {})
                tokens = payload.get("tokens", payload if isinstance(payload, list) else [])
                if tokens:
                    st.dataframe(pd.DataFrame(tokens), use_container_width=True, hide_index=True)
                else:
                    st.info("No tokens found.")
            else:
                status_code = response.get("status_code")
                if status_code == 401:
                    st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
                else:
                    st.error(response.get("error", "Failed to fetch tokens."))

with tabs[2]:
    st.subheader("Pools")
    st.caption("Add custom pools for supported connectors. Restart Gateway after adding pools.")

    with st.form("gateway_add_pool"):
        connector_name = select_connector("Connector", "pool_connector", connectors_list)
        network_id = select_network("Network", "pool_network", network_options)
        pool_type = st.selectbox("Pool Type", ["clmm", "amm"])

        col1, col2, col3 = st.columns(3)
        with col1:
            base_symbol = st.text_input("Base Symbol")
        with col2:
            quote_symbol = st.text_input("Quote Symbol")
        with col3:
            fee_pct = st.number_input(
                "Fee Pct (optional)",
                min_value=0.0,
                step=0.01,
                format="%.4f",
                value=0.0,
            )

        col1, col2 = st.columns(2)
        with col1:
            base_address = st.text_input("Base Token Address")
        with col2:
            quote_address = st.text_input("Quote Token Address")

        pool_address = st.text_input("Pool Address")
        submitted = st.form_submit_button("Add Pool")

        if submitted:
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
                        st.error(response.get("error", "Failed to add pool."))

with tabs[3]:
    st.subheader("Connectors")
    if connectors_response.get("ok"):
        if connectors_rows:
            st.dataframe(pd.DataFrame(connectors_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No connectors reported by Gateway.")
    else:
        st.error("Failed to fetch connectors from Gateway API.")
