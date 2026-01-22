import re
import secrets
import time
from decimal import Decimal

import pandas as pd
import streamlit as st

from frontend.st_utils import backend_api_request, get_backend_api_client, initialize_st_page

UNLIMITED_ALLOWANCE_THRESHOLD = Decimal("10000000000")

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


def split_trading_pair(trading_pair: str):
    if not trading_pair or "-" not in trading_pair:
        return None, None
    base, quote = trading_pair.split("-", 1)
    base = base.strip()
    quote = quote.strip()
    if not base or not quote:
        return None, None
    return base, quote


def split_network_id(network_id: str):
    if not network_id:
        return "", ""
    if "-" in network_id:
        chain, network = network_id.split("-", 1)
        return chain, network
    return network_id, ""


def parse_decimal_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"unlimited", "infinite", "infinity"}:
            return UNLIMITED_ALLOWANCE_THRESHOLD
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def is_gateway_connector(connector_name: str) -> bool:
    return isinstance(connector_name, str) and "/" in connector_name


def shorten_address(address: str) -> str:
    if not address:
        return "-"
    address = str(address)
    if len(address) <= 12:
        return address
    return f"{address[:6]}...{address[-4:]}"


def resolve_default_wallet(wallets, chain: str):
    if not wallets or not chain:
        return None
    for wallet in wallets:
        if wallet.get("chain") == chain and wallet.get("isDefault") and wallet.get("address"):
            return wallet.get("address")
    return None


def build_controller_config_map(controller_configs):
    config_map = {}
    if not isinstance(controller_configs, list):
        return config_map
    for config in controller_configs:
        if not isinstance(config, dict):
            continue
        config_id = config.get("id") or config.get("config", {}).get("id")
        if not config_id:
            continue
        config_data = config.get("config", config)
        config_map[config_id] = config_data
    return config_map


def build_approval_plan(selected_controllers, controller_config_map):
    plan = []
    for config_id in selected_controllers:
        config = controller_config_map.get(config_id)
        if not isinstance(config, dict):
            continue
        trading_pair = config.get("trading_pair", "")
        base_token, quote_token = split_trading_pair(trading_pair)
        tokens = [token for token in (base_token, quote_token) if token]
        if not tokens:
            continue
        connector_name = config.get("connector_name")
        router_connector = config.get("router_connector")
        spenders = []
        if is_gateway_connector(connector_name):
            spenders.append(connector_name)
        if config.get("auto_swap_enabled", False) and is_gateway_connector(router_connector):
            spenders.append(router_connector)
        spenders = list(dict.fromkeys([spender for spender in spenders if spender]))
        if not spenders:
            continue
        plan.append({
            "config_id": config_id,
            "controller_name": config.get("controller_name", config_id),
            "trading_pair": trading_pair,
            "pool_address": config.get("pool_address", ""),
            "tokens": tokens,
            "spenders": spenders,
        })
    return plan


def evaluate_controller_config(config_id, config, gateway_network_id):
    issues = []
    connector_name = config.get("connector_name")
    trading_pair = config.get("trading_pair")
    router_connector = config.get("router_connector")

    if not connector_name:
        issues.append({
            "Config": config_id,
            "Issue": "Missing connector_name",
            "Field": "connector_name",
            "Fix": "Set a gateway or exchange connector.",
        })

    if not trading_pair or "-" not in str(trading_pair):
        issues.append({
            "Config": config_id,
            "Issue": "Invalid trading_pair",
            "Field": "trading_pair",
            "Fix": "Use BASE-QUOTE format.",
        })

    if "pool_address" in config and not config.get("pool_address"):
        issues.append({
            "Config": config_id,
            "Issue": "Missing pool_address",
            "Field": "pool_address",
            "Fix": "Provide a pool address or remove the key.",
        })

    base_amount = parse_decimal_value(config.get("base_amount"))
    quote_amount = parse_decimal_value(config.get("quote_amount"))
    if base_amount is None and quote_amount is None:
        issues.append({
            "Config": config_id,
            "Issue": "Budget is zero",
            "Field": "base_amount / quote_amount",
            "Fix": "Set at least one amount above 0.",
        })

    if config.get("auto_swap_enabled") and not router_connector:
        issues.append({
            "Config": config_id,
            "Issue": "auto_swap_enabled without router_connector",
            "Field": "router_connector",
            "Fix": "Set router_connector or disable auto_swap_enabled.",
        })

    if connector_name and is_gateway_connector(connector_name) and not gateway_network_id:
        issues.append({
            "Config": config_id,
            "Issue": "Gateway network not selected",
            "Field": "gateway_network_id",
            "Fix": "Select a Gateway network in overrides.",
        })

    return issues


def render_config_health(selected_controllers, controller_configs, gateway_network_id):
    if not selected_controllers:
        return

    controller_config_map = build_controller_config_map(controller_configs)
    issues = []
    gateway_configs = 0

    for config_id in selected_controllers:
        config = controller_config_map.get(config_id)
        if not isinstance(config, dict):
            issues.append({
                "Config": config_id,
                "Issue": "Config not found",
                "Field": "id",
                "Fix": "Recreate the controller config.",
            })
            continue
        if is_gateway_connector(config.get("connector_name")):
            gateway_configs += 1
        issues.extend(evaluate_controller_config(config_id, config, gateway_network_id))

    with st.container(border=True):
        st.info("Config health checks for the selected controllers.")
        metrics = st.columns(3)
        metrics[0].metric("Selected", len(selected_controllers))
        metrics[1].metric("Gateway configs", gateway_configs)
        metrics[2].metric("Issues", len(issues))

        if issues:
            st.warning("Fix the items below to avoid deployment failures.")
            st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
        else:
            st.success("No config issues detected.")


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
        st.session_state["gateway_wallets"] = wallets
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


def render_approval_gate(
    selected_controllers,
    controller_configs,
    gateway_network_id,
    gateway_wallet_address,
):
    if not selected_controllers:
        return True

    controller_config_map = build_controller_config_map(controller_configs)
    plan = build_approval_plan(selected_controllers, controller_config_map)
    if not plan:
        st.info("No Gateway approvals required for the selected controllers.")
        return True

    if not gateway_network_id:
        st.warning("Select a Gateway network to check approvals.")
        return False

    chain, _ = split_network_id(gateway_network_id)
    if chain and chain != "ethereum":
        st.info("Selected network is not EVM. Allowances are not required.")
        return True

    wallets = st.session_state.get("gateway_wallets") or get_gateway_wallets()
    wallet_address = gateway_wallet_address or resolve_default_wallet(wallets, chain)
    if not wallet_address:
        st.warning("No default wallet found for this chain. Choose a wallet in Gateway overrides.")
        return False

    with st.container(border=True):
        st.markdown("**Approval Gate**")
        st.caption("Verify token allowances before deployment to avoid failed LP opens.")
        st.write(f"Network: {gateway_network_id} | Wallet: {shorten_address(wallet_address)}")
        st.caption("Approval target: unlimited. Allowance >= 1e10 is treated as unlimited.")

    spender_tokens = {}
    for item in plan:
        for spender in item["spenders"]:
            spender_tokens.setdefault(spender, set()).update(item["tokens"])

    if "approval_cache" not in st.session_state:
        st.session_state["approval_cache"] = {}
    if "approval_errors" not in st.session_state:
        st.session_state["approval_errors"] = {}

    signature = (
        tuple(sorted(selected_controllers)),
        gateway_network_id,
        wallet_address,
    )
    if st.session_state.get("approval_signature") != signature:
        st.session_state["approval_signature"] = signature
        st.session_state["approval_cache"] = {}
        st.session_state["approval_errors"] = {}
        st.session_state["approval_checked"] = False

    check_clicked = st.button("🔍 Check approvals", use_container_width=True)
    should_check = check_clicked or not st.session_state.get("approval_checked", False)

    if should_check:
        with st.spinner("Fetching allowances..."):
            for spender, tokens in spender_tokens.items():
                payload = {
                    "network_id": gateway_network_id,
                    "address": wallet_address,
                    "tokens": sorted(tokens),
                    "spender": spender,
                }
                response = backend_api_request(
                    "POST",
                    "/gateway/allowances",
                    json_body=payload,
                    timeout=60,
                )
                if response.get("ok"):
                    data = response.get("data", {})
                    approvals = data.get("approvals", {}) or {}
                    st.session_state["approval_cache"][spender] = approvals
                    st.session_state["approval_errors"].pop(spender, None)
                else:
                    error_msg = response.get("error", "Failed to fetch allowances.")
                    st.session_state["approval_errors"][spender] = error_msg
            st.session_state["approval_checked"] = True

    errors = st.session_state.get("approval_errors", {})
    for spender, error_msg in errors.items():
        st.error(f"{spender}: {error_msg}")

    overview_rows = []
    for item in plan:
        overview_rows.append({
            "Controller": item["controller_name"],
            "Trading Pair": item["trading_pair"],
            "Pool": shorten_address(item["pool_address"]),
            "Spenders": ", ".join(item["spenders"]),
            "Tokens": ", ".join(item["tokens"]),
        })
    if overview_rows:
        st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)

    allowance_rows = []
    missing = []
    approval_ready = True

    for item in plan:
        for spender in item["spenders"]:
            approvals = st.session_state.get("approval_cache", {}).get(spender)
            for token in item["tokens"]:
                allowance_raw = None if approvals is None else approvals.get(token)
                allowance_value = parse_decimal_value(allowance_raw)
                status = "Not checked"
                meets = False
                if approvals is not None:
                    meets = allowance_value is not None and allowance_value >= UNLIMITED_ALLOWANCE_THRESHOLD
                    status = "Approved" if meets else "Needs approval"

                allowance_rows.append({
                    "Controller": item["controller_name"],
                    "Spender": spender,
                    "Token": token,
                    "Required": "Unlimited",
                    "Allowance": "-" if allowance_raw is None else str(allowance_raw),
                    "Status": status,
                })

                if status != "Approved":
                    approval_ready = False
                    missing.append({
                        "controller": item["controller_name"],
                        "spender": spender,
                        "token": token,
                        "allowance": allowance_raw,
                    })

    if allowance_rows:
        st.dataframe(pd.DataFrame(allowance_rows), use_container_width=True, hide_index=True)

    if missing:
        st.warning("Approvals required before deployment.")
        for item in missing:
            cols = st.columns([3, 3, 2, 2])
            cols[0].markdown(f"**{item['controller']}**")
            cols[1].markdown(f"{item['token']} -> {item['spender']}")
            cols[2].markdown("Need: unlimited")
            approve_key = f"approve_{item['controller']}_{item['spender']}_{item['token']}"
            approve_key = re.sub(r"[^a-zA-Z0-9_-]+", "_", approve_key)
            if cols[3].button("Approve", key=approve_key, use_container_width=True):
                approve_payload = {
                    "network_id": gateway_network_id,
                    "address": wallet_address,
                    "token": item["token"],
                    "spender": item["spender"],
                }
                response = backend_api_request(
                    "POST",
                    "/gateway/approve",
                    json_body=approve_payload,
                    timeout=60,
                )
                if response.get("ok"):
                    st.success(f"Approval submitted for {item['token']} on {item['spender']}.")
                    st.session_state["approval_cache"].pop(item["spender"], None)
                    st.session_state["approval_checked"] = False
                else:
                    st.error(response.get("error", "Approval failed."))
    else:
        st.success("All approvals are ready.")

    return approval_ready


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

    full_bot_name = bot_name

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
            payload = response.get("data", {})
            deployed_name = payload.get("unique_instance_name") or full_bot_name
            st.success(f"Successfully deployed bot: {deployed_name}")
            normalized_from = payload.get("normalized_from")
            if normalized_from and normalized_from != deployed_name:
                st.info(f"Instance name normalized from '{normalized_from}' to '{deployed_name}'.")
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
        payload = response.get("data", {})
        deployed_name = payload.get("instance_name") or full_bot_name
        st.success(f"Successfully deployed bot: {deployed_name}")
        normalized_from = payload.get("normalized_from")
        if normalized_from and normalized_from != deployed_name:
            st.info(f"Instance name normalized from '{normalized_from}' to '{deployed_name}'.")
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
                st.switch_page("frontend/pages/orchestration/instances/app.py")

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

            if selected_controllers:
                render_config_health(
                    selected_controllers,
                    all_controllers_config,
                    gateway_network_id,
                )

            approval_ready = True
            if selected_controllers:
                approval_ready = render_approval_gate(
                    selected_controllers,
                    all_controllers_config,
                    gateway_network_id,
                    gateway_wallet_address,
                )

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
                deploy_disabled = not selected_controllers or not approval_ready
                deploy_button_style = "primary" if not deploy_disabled else "secondary"
                if st.button(
                    "🚀 Deploy Bot",
                    type=deploy_button_style,
                    use_container_width=True,
                    disabled=deploy_disabled,
                ):
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
                                st.switch_page("frontend/pages/orchestration/instances/app.py")
                    else:
                        st.warning("Please select at least one controller to deploy")
                if selected_controllers and not approval_ready:
                    st.warning("Resolve approvals above before deploying.")

        else:
            st.warning("⚠️ No controller configurations available. Please create some configurations first.")
