"""Deploy page - Deploy new LP strategies."""
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="🚀", show_readme=False)

# Default images
DOCKER_IMAGES = [
    "hummingbot/hummingbot:latest",
    "hummingbot/hummingbot:development",
]

api = get_backend_api_client()


def generate_instance_name(config_name: str = None) -> str:
    """Generate instance name based on config name with timestamp suffix."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if config_name:
        # Remove .yml extension if present and clean the name
        base_name = config_name.replace(".yml", "").replace(".yaml", "")
        # Truncate if too long (keep it reasonable for container names)
        if len(base_name) > 30:
            base_name = base_name[:30]
        return f"{base_name}-{timestamp}"
    return f"lp-strategy-{timestamp}"


# Page Header
st.title("🚀 Deploy Strategy")
st.subheader("Configure and deploy your LP trading strategy")

# Configuration Selection Section (moved to top so we can generate instance name based on config)
with st.container(border=True):
    st.success("🎛️ **Configuration Selection:** Select the strategy configuration to deploy")

    # Fetch available configurations
    try:
        configs = api.list_script_configs()
        config_list = [c.get("config_name") for c in configs if c.get("config_name")]
    except Exception:
        config_list = []

    # Fetch available scripts
    try:
        scripts = api.list_scripts()
    except Exception:
        scripts = ["gateway_lp_guarded"]

    # Deployment method
    deploy_method = st.radio(
        "Deployment Method",
        options=["From Existing Config", "Quick Deploy (Script Only)"],
        horizontal=True,
        help="Choose how to deploy the strategy",
    )

    st.divider()

    selected_config = None
    selected_script = None

    if deploy_method == "From Existing Config":
        if not config_list:
            st.warning("⚠️ No configurations found. Please create a configuration first in the Config page.")
        else:
            # Build data for config selection table
            data = []
            for config_name in config_list:
                try:
                    config_data = api.get_script_config(config_name)
                    script_file = config_data.get("script_file_name", "Unknown")
                    controllers = config_data.get("controllers_config", [])
                    data.append({
                        "Select": False,
                        "Config Name": config_name,
                        "Script": script_file,
                        "Controllers": len(controllers) if isinstance(controllers, list) else 0,
                    })
                except Exception:
                    data.append({
                        "Select": False,
                        "Config Name": config_name,
                        "Script": "Unknown",
                        "Controllers": 0,
                    })

            if data:
                df = pd.DataFrame(data)

                edited_df = st.data_editor(
                    df,
                    column_config={
                        "Select": st.column_config.CheckboxColumn(
                            "Select",
                            help="Select configuration to deploy",
                            default=False,
                        ),
                    },
                    disabled=[col for col in df.columns if col != "Select"],
                    hide_index=True,
                    use_container_width=True,
                    key="config_table",
                )

                # Get selected config
                selected_configs = [
                    row["Config Name"]
                    for _, row in edited_df.iterrows()
                    if row["Select"]
                ]
                selected_config = selected_configs[0] if selected_configs else None

                if selected_config:
                    st.success(f"✅ Selected: {selected_config}")

                    # Show config preview
                    with st.expander("📄 Configuration Preview"):
                        try:
                            config_data = api.get_script_config(selected_config)
                            st.json(config_data)
                        except Exception as e:
                            st.error(f"Error loading config: {e}")
    else:
        # Quick deploy - select script
        lp_scripts = [s for s in scripts if any(x in s.lower() for x in ["lp", "gateway", "amm"])]
        if not lp_scripts:
            lp_scripts = scripts if scripts else ["gateway_lp_guarded"]

        selected_script = st.selectbox(
            "Script",
            options=lp_scripts,
            help="Select the strategy script",
        )

# Bot Configuration Section (now comes after config selection)
with st.container(border=True):
    st.info("🤖 **Bot Configuration:** Set up your bot instance with basic configuration")

    col1, col2, col3 = st.columns(3)

    with col1:
        # Generate instance name based on selected config
        default_name = generate_instance_name(selected_config)
        instance_name = st.text_input(
            "Instance Name",
            value=default_name,
            placeholder="Auto-generated from config name",
            help="Unique name for this strategy instance (auto-generated from config name + timestamp)",
        )

    with col2:
        try:
            available_credentials = api.list_accounts() if hasattr(api, 'list_accounts') else ["master_account"]
            credentials = st.selectbox(
                "Credentials Profile",
                options=available_credentials if available_credentials else ["master_account"],
                help="Credentials profile for API keys",
            )
        except Exception:
            credentials = st.text_input(
                "Credentials Profile",
                value="master_account",
                help="Credentials profile for API keys",
            )

    with col3:
        try:
            all_images = api.get_available_images("hummingbot") if hasattr(api, 'get_available_images') else DOCKER_IMAGES
            if not all_images:
                all_images = DOCKER_IMAGES
        except Exception:
            all_images = DOCKER_IMAGES

        image_name = st.selectbox(
            "Hummingbot Image",
            options=all_images,
            help="Hummingbot Docker image to use",
        )

# Deploy Actions Section
with st.container(border=True):
    st.warning("⚡ **Deploy Actions:** Launch your trading bot")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("🗑️ Clear Selection", use_container_width=True):
            st.rerun()

    with col2:
        can_deploy = bool(instance_name) and (selected_config or deploy_method == "Quick Deploy (Script Only)")
        deploy_type = "primary" if can_deploy else "secondary"

        if st.button("🚀 Deploy Bot", type=deploy_type, use_container_width=True):
            if not instance_name:
                st.error("Instance name is required")
            elif deploy_method == "From Existing Config" and not selected_config:
                st.error("Please select a configuration")
            else:
                with st.spinner("🚀 Starting Bot... This process may take a few seconds"):
                    try:
                        if deploy_method == "From Existing Config" and selected_config:
                            config_data = api.get_script_config(selected_config)
                            script_name = config_data.get("script_file_name", "gateway_lp_guarded.py")

                            result = api.deploy_v2_script(
                                instance_name=instance_name,
                                script=script_name,
                                script_config=f"{selected_config}.yml",
                                credentials_profile=credentials,
                                image=image_name,
                            )
                        else:
                            script_name = f"{selected_script}.py" if not selected_script.endswith(".py") else selected_script

                            result = api.deploy_v2_script(
                                instance_name=instance_name,
                                script=script_name,
                                credentials_profile=credentials,
                                image=image_name,
                            )

                        if result.get("success"):
                            st.success(f"✅ Successfully deployed: {instance_name}")
                            st.balloons()

                            with st.expander("📋 Deployment Details", expanded=True):
                                st.json(result)

                            time.sleep(2)
                            st.rerun()
                        else:
                            st.error(f"❌ Deployment failed: {result.get('error', 'Unknown error')}")

                    except Exception as e:
                        st.error(f"❌ Deployment error: {e}")

# Recent Deployments Section
st.markdown("---")
with st.container(border=True):
    st.info("📋 **Recent Deployments:** Previously deployed bot instances")

    try:
        runs = api.get_bot_runs(limit=5)
        runs_data = runs.get("data", [])

        if runs_data:
            data = []
            for run in runs_data:
                data.append({
                    "Bot Name": run.get("bot_name", "Unknown"),
                    "Config": run.get("config_name", "N/A"),
                    "Status": run.get("run_status", "UNKNOWN"),
                    "Deployed At": run.get("deployed_at", "")[:19] if run.get("deployed_at") else "N/A",
                })

            st.dataframe(
                pd.DataFrame(data),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No recent deployments found.")

    except Exception:
        st.info("No recent deployments available.")
