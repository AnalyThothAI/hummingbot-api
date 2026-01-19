"""Config page - Configuration management."""
import yaml
import streamlit as st

from st_utils import initialize_st_page, get_backend_api_client

initialize_st_page(icon="⚙️", show_readme=False)

api = get_backend_api_client()

# Page Header
st.title("⚙️ Configuration")
st.subheader("Manage your LP strategy configurations")

# Tabs for different views
tab1, tab2 = st.tabs(["📝 Edit Config", "➕ New Config"])

with tab1:
    # Config editor section
    with st.container(border=True):
        st.info("📂 **Configuration List:** Select a configuration to edit")

        col1, col2 = st.columns([1, 2])

        with col1:
            try:
                configs = api.list_script_configs()

                if not configs:
                    st.warning("No configurations found")
                else:
                    # Search filter
                    search = st.text_input(
                        "🔍 Search",
                        placeholder="Filter configurations...",
                    )

                    if search:
                        configs = [c for c in configs if search.lower() in c.get("config_name", "").lower()]

                    st.divider()

                    # Config list
                    for config in configs:
                        config_name = config.get("config_name", "Unknown")

                        if st.button(
                            f"📄 {config_name}",
                            key=f"config_{config_name}",
                            use_container_width=True,
                        ):
                            st.session_state.editing_config = config_name
                            st.rerun()

            except Exception as e:
                st.error(f"Error loading configurations: {e}")

        with col2:
            editing_config = st.session_state.get("editing_config")

            if not editing_config:
                st.info("👈 Select a configuration from the list to edit")
            else:
                with st.container(border=True):
                    st.success(f"📝 **Editing:** {editing_config}")

                    try:
                        config_data = api.get_script_config(editing_config)
                    except Exception as e:
                        st.error(f"Error loading configuration: {e}")
                        config_data = {}

                    if config_data:
                        # Convert to YAML
                        yaml_content = yaml.dump(config_data, default_flow_style=False, sort_keys=False)

                        # Editor tabs
                        view_tab, yaml_tab = st.tabs(["📋 Form View", "📝 YAML View"])

                        with view_tab:
                            # Form-based editor
                            edited_config = {}

                            for key, value in config_data.items():
                                if isinstance(value, bool):
                                    edited_config[key] = st.checkbox(key, value=value)
                                elif isinstance(value, int):
                                    edited_config[key] = st.number_input(key, value=value, step=1)
                                elif isinstance(value, float):
                                    edited_config[key] = st.number_input(key, value=value, format="%.6f")
                                elif isinstance(value, str):
                                    edited_config[key] = st.text_input(key, value=value)
                                elif isinstance(value, list):
                                    if all(isinstance(item, str) for item in value):
                                        items_str = "\n".join(value)
                                        edited_str = st.text_area(f"{key} (one per line)", value=items_str, height=100)
                                        edited_config[key] = [item.strip() for item in edited_str.split("\n") if item.strip()]
                                    else:
                                        yaml_str = yaml.dump(value, default_flow_style=False)
                                        edited_yaml = st.text_area(f"{key} (YAML)", value=yaml_str, height=150)
                                        try:
                                            edited_config[key] = yaml.safe_load(edited_yaml) or []
                                        except yaml.YAMLError:
                                            edited_config[key] = value
                                elif isinstance(value, dict):
                                    yaml_str = yaml.dump(value, default_flow_style=False)
                                    edited_yaml = st.text_area(f"{key} (YAML)", value=yaml_str, height=150)
                                    try:
                                        edited_config[key] = yaml.safe_load(edited_yaml) or {}
                                    except yaml.YAMLError:
                                        edited_config[key] = value
                                else:
                                    edited_config[key] = st.text_input(key, value=str(value) if value else "")

                        with yaml_tab:
                            edited_yaml = st.text_area(
                                "YAML Content",
                                value=yaml_content,
                                height=400,
                                label_visibility="collapsed",
                            )
                            try:
                                edited_config = yaml.safe_load(edited_yaml) or {}
                            except yaml.YAMLError as e:
                                st.error(f"Invalid YAML: {e}")
                                edited_config = config_data

                        # Action buttons
                        st.divider()
                        col1, col2, col3 = st.columns(3)

                        with col1:
                            if st.button("💾 Save", type="primary", use_container_width=True):
                                try:
                                    api.save_script_config(editing_config, edited_config)
                                    st.success("✅ Configuration saved!")
                                except Exception as e:
                                    st.error(f"Error saving: {e}")

                        with col2:
                            if st.button("🗑️ Delete", type="secondary", use_container_width=True):
                                if st.session_state.get(f"confirm_delete_{editing_config}"):
                                    try:
                                        api.delete_script_config(editing_config)
                                        st.session_state.editing_config = None
                                        st.success("✅ Configuration deleted!")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Error deleting: {e}")
                                else:
                                    st.session_state[f"confirm_delete_{editing_config}"] = True
                                    st.warning("⚠️ Click Delete again to confirm")

                        with col3:
                            if st.button("❌ Cancel", use_container_width=True):
                                st.session_state.editing_config = None
                                st.rerun()

with tab2:
    # New configuration form
    with st.container(border=True):
        st.success("➕ **Create New Configuration:** Set up a new strategy configuration")

        # Fetch available scripts
        try:
            scripts = api.list_scripts()
        except Exception:
            scripts = ["gateway_lp_guarded"]

        col1, col2 = st.columns(2)

        with col1:
            config_name = st.text_input(
                "Configuration Name",
                placeholder="my-strategy-config",
                help="Unique name for this configuration",
            )

        with col2:
            script = st.selectbox(
                "Script",
                options=scripts if scripts else ["gateway_lp_guarded"],
                help="Script to use for this configuration",
            )

        st.divider()

        # Default YAML content
        default_yaml = f"""# Strategy Configuration
script_file_name: {script}.py
controllers_config: []
candles_config: []
markets: {{}}
"""

        yaml_content = st.text_area(
            "Configuration (YAML)",
            value=default_yaml,
            height=300,
            help="Edit the YAML configuration for your strategy",
        )

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            if st.button("🗑️ Clear", use_container_width=True):
                st.rerun()

        with col2:
            if st.button("✅ Create Configuration", type="primary", use_container_width=True):
                if not config_name:
                    st.error("❌ Configuration name is required")
                else:
                    try:
                        config_data = yaml.safe_load(yaml_content) or {}
                        api.save_script_config(config_name, config_data)
                        st.success(f"✅ Configuration '{config_name}' created!")
                        st.session_state.editing_config = config_name
                        st.balloons()
                        st.rerun()
                    except yaml.YAMLError as e:
                        st.error(f"❌ Invalid YAML: {e}")
                    except Exception as e:
                        st.error(f"❌ Error creating configuration: {e}")
