import re
import secrets
import time

import pandas as pd
import streamlit as st

from frontend.st_utils import backend_api_request, get_backend_api_client, initialize_st_page

initialize_st_page(icon="🙌", show_readme=False)

# Initialize backend client
backend_api_client = get_backend_api_client()


def get_controller_configs():
    """Get all controller configurations using the new API."""
    try:
        return backend_api_client.controllers.list_controller_configs()
    except Exception as e:
        st.error(f"Failed to fetch controller configs: {e}")
        return []


def get_script_configs():
    """Get script configurations from the backend."""
    response = backend_api_request("GET", "/scripts/configs/")
    if response.get("ok"):
        return response.get("data", [])
    st.error("Failed to fetch script configs. Check API connectivity and auth.")
    return []


def get_scripts():
    """Get available script files from the backend."""
    response = backend_api_request("GET", "/scripts/")
    if response.get("ok"):
        return response.get("data", [])
    st.error("Failed to fetch scripts. Check API connectivity and auth.")
    return []


def get_gateway_networks():
    """Get Gateway network options for chain-network selection."""
    response = backend_api_request("GET", "/gateway/networks")
    if response.get("ok"):
        networks = response.get("data", {}).get("networks", [])
        return [item for item in networks if isinstance(item, dict) and item.get("network_id")]
    st.warning("Failed to fetch Gateway networks.")
    return []


def get_gateway_wallets():
    """Get Gateway wallets for connector configuration."""
    response = backend_api_request("GET", "/accounts/gateway/wallets")
    if response.get("ok"):
        wallets = response.get("data", [])
        return wallets if isinstance(wallets, list) else []
    st.warning("Failed to fetch Gateway wallets.")
    return []


def filter_hummingbot_images(images):
    """Filter images to only show Hummingbot-related ones."""
    hummingbot_images = []
    pattern = r'.+/hummingbot(?!-api)[^:]*:'

    for image in images:
        try:
            if re.match(pattern, image):
                hummingbot_images.append(image)
        except Exception:
            continue

    return hummingbot_images


def normalize_script_name(script_name: str) -> str:
    if not script_name:
        return ""
    base_name = script_name.replace(".py", "")
    base_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", base_name).strip("-")
    return base_name.lower()


def generate_instance_name(script_name: str) -> str:
    base_name = normalize_script_name(script_name) or "bot"
    timestamp = time.strftime("%Y%m%d-%H%M")
    suffix = secrets.token_hex(2)
    return f"{base_name}-{timestamp}-{suffix}"


def mark_bot_name_overridden():
    st.session_state["bot_name_overridden"] = True


def ensure_bot_name_from_script(script_name: str):
    if "bot_name_overridden" not in st.session_state:
        st.session_state["bot_name_overridden"] = False
    if "last_script_selection" not in st.session_state:
        st.session_state["last_script_selection"] = None

    if script_name and not st.session_state["bot_name_overridden"]:
        if st.session_state["last_script_selection"] != script_name:
            st.session_state["bot_name_input"] = generate_instance_name(script_name)
            st.session_state["last_script_selection"] = script_name


def render_bot_config(auto_name_hint: bool = False):
    with st.container(border=True):
        st.info("🤖 **Bot Configuration:** Set up your bot instance with basic configuration")

        col1, col2, col3 = st.columns(3)

        with col1:
            bot_name = st.text_input(
                "Instance Name",
                placeholder="Enter a unique name for your bot instance",
                key="bot_name_input",
                on_change=mark_bot_name_overridden,
                help="Auto-generated from script selection; edit if needed." if auto_name_hint else None,
            )

        with col2:
            try:
                available_credentials = backend_api_client.accounts.list_accounts()
                credentials = st.selectbox(
                    "Credentials Profile",
                    options=available_credentials,
                    index=0,
                    key="credentials_select"
                )
            except Exception as e:
                st.error(f"Failed to fetch credentials: {e}")
                credentials = st.text_input(
                    "Credentials Profile",
                    value="master_account",
                    key="credentials_input"
                )

        with col3:
            try:
                all_images = backend_api_client.docker.get_available_images("hummingbot")
                available_images = filter_hummingbot_images(all_images)

                if not available_images:
                    available_images = ["qinghuanlyke/hummingbot-lp:latest"]

                default_image = "qinghuanlyke/hummingbot-lp:latest"
                if default_image not in available_images:
                    available_images.insert(0, default_image)

                image_name = st.selectbox(
                    "Hummingbot Image",
                    options=available_images,
                    index=0,
                    key="image_select"
                )
            except Exception as e:
                st.error(f"Failed to fetch available images: {e}")
                image_name = st.text_input(
                    "Hummingbot Image",
                    value="qinghuanlyke/hummingbot-lp:latest",
                    key="image_input"
                )

    return bot_name, credentials, image_name


def render_gateway_overrides():
    with st.container(border=True):
        st.info("🔌 **Gateway Overrides:** Select network and wallet for Gateway connectors")

        networks = get_gateway_networks()
        network_map = {item["network_id"]: item for item in networks}
        network_options = ["(select network)"] + sorted(network_map.keys())
        preferred_network = "ethereum-bsc"
        if preferred_network not in network_options:
            network_options.insert(1, preferred_network)

        default_network_index = network_options.index(preferred_network) if preferred_network in network_options else 0
        selected_network_id = st.selectbox(
            "Gateway Network (chain-network)",
            options=network_options,
            index=default_network_index,
        )

        selected_chain = None
        if selected_network_id != "(select network)":
            selected_chain = network_map.get(selected_network_id, {}).get("chain")

        wallets = get_gateway_wallets()
        filtered_wallets = [
            wallet for wallet in wallets if wallet.get("chain") == selected_chain
        ] if selected_chain else []

        wallet_options = ["(gateway default)"] + [
            wallet.get("address") for wallet in filtered_wallets if wallet.get("address")
        ]

        selected_wallet = st.selectbox(
            "Gateway Wallet",
            options=wallet_options,
            index=0,
        )

        st.caption("Gateway overrides update chain defaults globally (shared Gateway).")

    network_value = None if selected_network_id == "(select network)" else selected_network_id
    wallet_value = None if selected_wallet == "(gateway default)" else selected_wallet
    return network_value, wallet_value


def launch_new_bot(
    bot_name,
    image_name,
    credentials,
    selected_controllers,
    max_global_drawdown,
    max_controller_drawdown,
    gateway_network_id,
    gateway_wallet_address,
):
    """Launch a new bot with the selected configuration."""
    if not bot_name:
        st.warning("You need to define the bot name.")
        return False
    if not image_name:
        st.warning("You need to select the hummingbot image.")
        return False
    if not selected_controllers:
        st.warning("You need to select the controllers configs. Please select at least one controller "
                   "config by clicking on the checkbox.")
        return False

    start_time_str = time.strftime("%Y%m%d-%H%M")
    full_bot_name = f"{bot_name}-{start_time_str}"

    try:
        deploy_config = {
            "instance_name": full_bot_name,
            "credentials_profile": credentials,
            "controllers_config": selected_controllers,
            "image": image_name,
        }

        if max_global_drawdown is not None and max_global_drawdown > 0:
            deploy_config["max_global_drawdown_quote"] = max_global_drawdown
        if max_controller_drawdown is not None and max_controller_drawdown > 0:
            deploy_config["max_controller_drawdown_quote"] = max_controller_drawdown
        if gateway_network_id:
            deploy_config["gateway_network_id"] = gateway_network_id
        if gateway_wallet_address:
            deploy_config["gateway_wallet_address"] = gateway_wallet_address
        response = backend_api_request(
            "POST",
            "/bot-orchestration/deploy-v2-controllers",
            json_body=deploy_config,
        )
        if response.get("ok"):
            st.success(f"Successfully deployed bot: {full_bot_name}")
            time.sleep(3)
            return True
        status_code = response.get("status_code")
        if status_code == 401:
            st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
        else:
            st.error(response.get("error", "Failed to deploy controller bot."))
        return False

    except Exception as e:
        st.error(f"Failed to deploy bot: {e}")
        return False


def launch_script_bot(
    bot_name,
    image_name,
    credentials,
    script_name,
    script_config,
    gateway_network_id,
    gateway_wallet_address,
):
    """Launch a new bot with a script and optional config."""
    if not bot_name:
        st.warning("You need to define the bot name.")
        return False
    if not image_name:
        st.warning("You need to select the hummingbot image.")
        return False
    if not script_name:
        st.warning("You need to select a script.")
        return False
    if not gateway_network_id:
        st.warning("You need to select a Gateway network.")
        return False
    full_bot_name = bot_name

    script_value = script_name if script_name.endswith(".py") else f"{script_name}.py"
    script_config_value = None
    if script_config:
        script_config_value = script_config if script_config.endswith(".yml") else f"{script_config}.yml"

    deploy_payload = {
        "instance_name": full_bot_name,
        "credentials_profile": credentials,
        "image": image_name,
        "script": script_value,
        "script_config": script_config_value,
        "gateway_network_id": gateway_network_id,
        "gateway_wallet_address": gateway_wallet_address,
    }

    response = backend_api_request("POST", "/bot-orchestration/deploy-v2-script", json_body=deploy_payload)
    if response.get("ok"):
        st.success(f"Successfully deployed bot: {full_bot_name}")
        time.sleep(3)
        return True

    status_code = response.get("status_code")
    if status_code == 401:
        st.error("Unauthorized. Check BACKEND_API_USERNAME and BACKEND_API_PASSWORD.")
    else:
        st.error(response.get("error", "Failed to deploy script bot."))
    return False


def delete_selected_configs(selected_controllers):
    """Delete selected controller configurations."""
    if selected_controllers:
        try:
            for config in selected_controllers:
                # Remove .yml extension if present
                config_name = config.replace(".yml", "")
                response = backend_api_client.controllers.delete_controller_config(config_name)
                st.success(f"Deleted {config_name}")
            return True

        except Exception as e:
            st.error(f"Failed to delete configs: {e}")
            return False
    else:
        st.warning("You need to select the controllers configs that you want to delete.")
        return False


# Page Header
st.title("🚀 Deploy Trading Bot")
st.subheader("Configure and deploy your automated trading strategy")

deploy_mode = st.radio(
    "Deployment Mode",
    options=["Controllers", "Script"],
    horizontal=True,
    help="Scripts deploy a single strategy file. Controllers deploy multiple controller configs.",
)

if "last_deploy_mode" not in st.session_state:
    st.session_state["last_deploy_mode"] = deploy_mode
elif st.session_state["last_deploy_mode"] != deploy_mode:
    if deploy_mode == "Script":
        st.session_state["bot_name_overridden"] = False
    st.session_state["last_deploy_mode"] = deploy_mode

if deploy_mode == "Script":
    with st.container(border=True):
        st.success("🧩 **Script Selection:** Choose a script and its configuration (if available)")

        script_configs = get_script_configs()
        scripts = get_scripts()

        config_map = {config.get("config_name"): config for config in script_configs if isinstance(config, dict)}
        config_names = sorted([name for name in config_map.keys() if name])

        default_config = None
        if "v2_meteora_tomato_sol" in config_names:
            default_config = "v2_meteora_tomato_sol"
        elif config_names:
            default_config = config_names[0]

        config_options = ["(none)"] + config_names
        config_index = config_options.index(default_config) if default_config else 0

        selected_config = st.selectbox(
            "Script Config (optional)",
            options=config_options,
            index=config_index,
        )

        inferred_script = None
        if selected_config != "(none)":
            config_entry = config_map.get(selected_config, {})
            script_file_name = config_entry.get("script_file_name")
            if script_file_name and script_file_name not in {"unknown", "error"}:
                inferred_script = script_file_name.replace(".py", "")

        script_options = sorted({script for script in scripts if script})
        preferred_scripts = ["v2_clmm_lp_recenter"]
        for preferred in reversed(preferred_scripts):
            if preferred in script_options:
                script_options.remove(preferred)
                script_options.insert(0, preferred)
        if inferred_script and inferred_script not in script_options:
            script_options.insert(0, inferred_script)

        if script_options:
            selected_script = st.selectbox(
                "Script File",
                options=script_options,
                index=script_options.index(inferred_script) if inferred_script in script_options else 0,
            )
        else:
            selected_script = st.text_input("Script File (without .py)")

        if not scripts:
            st.info("No scripts found. Add files under bots/scripts to enable script deployment.")

        if not script_configs:
            st.info("No script configs found. Add configs under bots/conf/scripts to select a config.")

    ensure_bot_name_from_script(selected_script)
    bot_name, credentials, image_name = render_bot_config(auto_name_hint=True)
    gateway_network_id, gateway_wallet_address = render_gateway_overrides()

    if st.button("🚀 Deploy Script Bot", type="primary", use_container_width=True):
        with st.spinner("🚀 Starting Bot... This process may take a few seconds"):
            if launch_script_bot(bot_name, image_name, credentials, selected_script,
                                 None if selected_config == "(none)" else selected_config,
                                 gateway_network_id,
                                 gateway_wallet_address):
                st.rerun()

else:
    bot_name, credentials, image_name = render_bot_config(auto_name_hint=False)
    gateway_network_id, gateway_wallet_address = render_gateway_overrides()

    # Risk Management Section
    with st.container(border=True):
        st.warning("⚠️ **Risk Management:** Set maximum drawdown limits in USDT to protect your capital")

        col1, col2 = st.columns(2)

        with col1:
            max_global_drawdown = st.number_input(
                "Max Global Drawdown (USDT)",
                min_value=0.0,
                value=0.0,
                step=100.0,
                format="%.2f",
                help="Maximum allowed drawdown across all controllers",
                key="global_drawdown_input"
            )

        with col2:
            max_controller_drawdown = st.number_input(
                "Max Controller Drawdown (USDT)",
                min_value=0.0,
                value=0.0,
                step=100.0,
                format="%.2f",
                help="Maximum allowed drawdown per controller",
                key="controller_drawdown_input"
            )

    # Controllers Section
    with st.container(border=True):
        st.success("🎛️ **Controller Selection:** Select the trading controllers you want to deploy with this bot instance")

        # Get controller configs
        all_controllers_config = get_controller_configs()

        # Prepare data for the table
        data = []
        for config in all_controllers_config:
            # Handle case where config might be a string instead of dict
            if isinstance(config, str):
                st.warning(f"Unexpected config format: {config}. Expected a dictionary.")
                continue

            # Handle both old and new config format
            config_name = config.get("id")
            if not config_name:
                # Skip configs without an ID
                st.warning(f"Config missing 'id' field: {config}")
                continue

            config_data = config.get("config", config)  # New format has config nested

            connector_name = config_data.get("connector_name", "Unknown")
            trading_pair = config_data.get("trading_pair", "Unknown")
            total_amount_quote = float(config_data.get("total_amount_quote", 0))

            # Extract controller info
            controller_name = config_data.get("controller_name", config_name)
            controller_type = config_data.get("controller_type", "generic")

            # Fix config base and version splitting
            config_parts = config_name.split("_")
            if len(config_parts) > 1:
                version = config_parts[-1]
                config_base = "_".join(config_parts[:-1])
            else:
                config_base = config_name
                version = "NaN"

            data.append({
                "Select": False,  # Checkbox column
                "Config Base": config_base,
                "Version": version,
                "Controller Name": controller_name,
                "Controller Type": controller_type,
                "Connector": connector_name,
                "Trading Pair": trading_pair,
                "Amount (USDT)": f"${total_amount_quote:,.2f}",
                "_config_name": config_name  # Hidden column for reference
            })

        # Display info and action buttons
        if data:
            # Create DataFrame
            df = pd.DataFrame(data)

            # Use data_editor with checkbox column for selection
            edited_df = st.data_editor(
                df,
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Select",
                        help="Select controllers to deploy or delete",
                        default=False,
                    ),
                    "_config_name": None,  # Hide this column
                },
                disabled=[col for col in df.columns if col != "Select"],  # Only allow editing the Select column
                hide_index=True,
                use_container_width=True,
                key="controller_table"
            )

            # Get selected controllers from the edited dataframe
            selected_controllers = [
                row["_config_name"]
                for _, row in edited_df.iterrows()
                if row["Select"]
            ]

            # Display selected count
            if selected_controllers:
                st.success(f"✅ {len(selected_controllers)} controller(s) selected for deployment")

            # Display action buttons
            st.divider()
            col1, col2 = st.columns(2)

            with col1:
                if st.button("🗑️ Delete Selected", type="secondary", use_container_width=True):
                    if selected_controllers:
                        if delete_selected_configs(selected_controllers):
                            st.rerun()
                    else:
                        st.warning("Please select at least one controller to delete")

            with col2:
                deploy_button_style = "primary" if selected_controllers else "secondary"
                if st.button("🚀 Deploy Bot", type=deploy_button_style, use_container_width=True):
                    if selected_controllers:
                        with st.spinner('🚀 Starting Bot... This process may take a few seconds'):
                            if launch_new_bot(
                                bot_name,
                                image_name,
                                credentials,
                                selected_controllers,
                                max_global_drawdown,
                                max_controller_drawdown,
                                gateway_network_id,
                                gateway_wallet_address,
                            ):
                                st.rerun()
                    else:
                        st.warning("Please select at least one controller to deploy")

        else:
            st.warning("⚠️ No controller configurations available. Please create some configurations first.")
