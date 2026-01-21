import nest_asyncio
import streamlit as st

from frontend.st_utils import get_backend_api_client, initialize_st_page

nest_asyncio.apply()

initialize_st_page(title="Credentials", icon="🔑")

# Page content
client = get_backend_api_client()
NUM_COLUMNS = 4


def list_connectors():
    try:
        return client.connectors.list_connectors()
    except Exception as e:
        st.warning(f"Failed to load connectors: {e}")
        return []


def get_connector_config_map(connector_name: str):
    if not connector_name or connector_name == "No connectors available":
        return {}, None
    try:
        return client.connectors.get_config_map(connector_name=connector_name), None
    except Exception as e:
        return {}, str(e)


def normalize_config_fields(config_map):
    if isinstance(config_map, dict):
        return list(config_map.keys())
    if isinstance(config_map, list):
        return [item for item in config_map if isinstance(item, str)]
    return []


@st.fragment
def accounts_section():
    # Get fresh accounts list
    accounts = client.accounts.list_accounts()

    if accounts:
        n_accounts = len(accounts)
        # Ensure master_account is first, but handle if it doesn't exist
        if "master_account" in accounts:
            accounts.remove("master_account")
            accounts.insert(0, "master_account")
        for i in range(0, n_accounts, NUM_COLUMNS):
            cols = st.columns(NUM_COLUMNS)
            for j, account in enumerate(accounts[i:i + NUM_COLUMNS]):
                with cols[j]:
                    st.subheader(f"🏦  {account}")
                    credentials = client.accounts.list_account_credentials(account)
                    st.json(credentials)
    else:
        st.write("No accounts available.")

    st.markdown("---")

    # Account management actions
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        # Section to create a new account
        st.header("Create a New Account")
        new_account_name = st.text_input("New Account Name")
        if st.button("Create Account"):
            new_account_name = new_account_name.replace(" ", "_")
            if new_account_name:
                if new_account_name in accounts:
                    st.warning(f"Account {new_account_name} already exists.")
                    st.stop()
                elif new_account_name == "" or all(char == "_" for char in new_account_name):
                    st.warning("Please enter a valid account name.")
                    st.stop()
                response = client.accounts.add_account(new_account_name)
                st.write(response)
                try:
                    st.rerun(scope="fragment")
                except Exception:
                    st.rerun()
            else:
                st.write("Please enter an account name.")

    with c2:
        # Section to delete an existing account
        st.header("Delete an Account")
        delete_account_name = st.selectbox("Select Account to Delete",
                                           options=accounts if accounts else ["No accounts available"], )
        if st.button("Delete Account"):
            if delete_account_name and delete_account_name != "No accounts available":
                response = client.accounts.delete_account(delete_account_name)
                st.warning(response)
                try:
                    st.rerun(scope="fragment")
                except Exception:
                    st.rerun()
            else:
                st.write("Please select a valid account.")

    with c3:
        # Section to delete a credential from an existing account
        st.header("Delete Credential")
        delete_account_cred_name = st.selectbox("Select the credentials account",
                                                options=accounts if accounts else ["No accounts available"], )
        credentials_data = client.accounts.list_account_credentials(delete_account_cred_name)
        # Handle different possible return formats
        if isinstance(credentials_data, list):
            # If it's a list of strings in format "connector.key"
            if credentials_data and isinstance(credentials_data[0], str):
                creds_for_account = [credential.split(".")[0] for credential in credentials_data]
            # If it's a list of dicts, extract connector names
            elif credentials_data and isinstance(credentials_data[0], dict):
                creds_for_account = list(
                    set([cred.get('connector', cred.get('connector_name', '')) for cred in credentials_data if
                         cred.get('connector') or cred.get('connector_name')]))
            else:
                creds_for_account = []
        elif isinstance(credentials_data, dict):
            # If it's a dict with connectors as keys
            creds_for_account = list(credentials_data.keys())
        else:
            creds_for_account = []
        delete_cred_name = st.selectbox("Select a Credential to Delete",
                                        options=creds_for_account if creds_for_account else [
                                            "No credentials available"])
        if st.button("Delete Credential"):
            if (delete_account_cred_name and delete_account_cred_name != "No accounts available") and \
                    (delete_cred_name and delete_cred_name != "No credentials available"):
                response = client.accounts.delete_credential(delete_account_cred_name, delete_cred_name)
                st.warning(response)
                try:
                    st.rerun(scope="fragment")
                except Exception:
                    st.rerun()
            else:
                st.write("Please select a valid account.")

    return accounts


accounts = accounts_section()

st.markdown("---")


# Section to add credentials
@st.fragment
def add_credentials_section():
    st.header("Add Credentials")
    c1, c2 = st.columns([1, 1])
    with c1:
        account_name = st.selectbox("Select Account", options=accounts if accounts else ["No accounts available"])
    with c2:
        all_connectors = list_connectors()
        connector_options = all_connectors if all_connectors else ["No connectors available"]
        binance_perpetual_index = connector_options.index(
            "binance_perpetual") if "binance_perpetual" in connector_options else 0
        connector_name = st.selectbox("Select Connector", options=connector_options, index=binance_perpetual_index)
        config_map, config_map_error = get_connector_config_map(connector_name)
        config_fields = normalize_config_fields(config_map)

    st.write(f"Configuration Map for {connector_name}:")
    config_inputs = {}

    # Custom logic for XRPL connector
    if connector_name == "xrpl":
        # Define custom XRPL fields with default values
        xrpl_fields = {
            "xrpl_secret_key": "",
            "wss_node_urls": "wss://xrplcluster.com,wss://s1.ripple.com,wss://s2.ripple.com",
        }

        # Display XRPL-specific fields
        for field, default_value in xrpl_fields.items():
            if field == "xrpl_secret_key":
                config_inputs[field] = st.text_input(field, type="password", key=f"{connector_name}_{field}")
            else:
                config_inputs[field] = st.text_input(field, value=default_value, key=f"{connector_name}_{field}")

        if st.button("Submit Credentials"):
            response = client.accounts.add_credential(account_name, connector_name, config_inputs)
            if response:
                st.success(f"✅ Successfully added {connector_name} connector to {account_name}!")
                try:
                    st.rerun(scope="fragment")
                except Exception:
                    st.rerun()
    elif config_map_error:
        st.warning(f"Could not get config map for {connector_name}: {config_map_error}")
    elif not config_fields:
        if connector_name != "No connectors available":
            if "/" in connector_name:
                st.info("Gateway connectors do not require API credentials. Configure wallets on the Gateway page.")
            else:
                st.info("No credentials required for this connector.")
    else:
        # Default behavior for other connectors
        cols = st.columns(NUM_COLUMNS)
        for i, config in enumerate(config_fields):
            with cols[i % (NUM_COLUMNS - 1)]:
                config_inputs[config] = st.text_input(config, type="password", key=f"{connector_name}_{config}")

        with cols[-1]:
            if st.button("Submit Credentials"):
                response = client.accounts.add_credential(account_name, connector_name, config_inputs)
                st.write(response)


add_credentials_section()
