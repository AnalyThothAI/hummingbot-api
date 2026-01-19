"""YAML configuration editor component for LP Dashboard."""
import streamlit as st
from typing import Any, Callable, Dict, Optional
import yaml


def render_config_editor(
    config_name: str,
    config_data: Dict[str, Any],
    on_save: Optional[Callable[[str, Dict], None]] = None,
    on_delete: Optional[Callable[[str], None]] = None,
    read_only: bool = False,
    show_raw: bool = True,
):
    """Render a YAML configuration editor.

    Args:
        config_name: Name of the configuration
        config_data: Configuration data as dictionary
        on_save: Callback when save button is clicked
        on_delete: Callback when delete button is clicked
        read_only: Whether the editor is read-only
        show_raw: Whether to show raw YAML view
    """
    st.subheader(f"Configuration: {config_name}")

    # Convert dict to YAML string
    yaml_str = yaml.dump(config_data, default_flow_style=False, sort_keys=False)

    # Create tabs for different views
    tab1, tab2 = st.tabs(["Form View", "YAML View"])

    with tab1:
        # Render form-based editor
        edited_config = _render_form_editor(config_data, read_only)

    with tab2:
        # Render raw YAML editor
        if show_raw:
            edited_yaml = st.text_area(
                "YAML Content",
                value=yaml_str,
                height=400,
                disabled=read_only,
                key=f"yaml_editor_{config_name}",
            )

            # Parse edited YAML
            if not read_only:
                try:
                    edited_config = yaml.safe_load(edited_yaml) or {}
                except yaml.YAMLError as e:
                    st.error(f"Invalid YAML: {e}")
                    edited_config = config_data

    # Action buttons
    if not read_only:
        col1, col2, col3 = st.columns([1, 1, 2])

        with col1:
            if st.button("Save", type="primary", key=f"save_{config_name}"):
                if on_save:
                    on_save(config_name, edited_config)
                    st.success(f"Configuration '{config_name}' saved!")

        with col2:
            if st.button("Delete", type="secondary", key=f"delete_{config_name}"):
                if on_delete:
                    on_delete(config_name)
                    st.warning(f"Configuration '{config_name}' deleted!")

        with col3:
            if st.button("Reset", key=f"reset_{config_name}"):
                st.rerun()

    return edited_config


def _render_form_editor(config: Dict[str, Any], read_only: bool) -> Dict[str, Any]:
    """Render a form-based configuration editor."""
    edited = {}

    for key, value in config.items():
        edited[key] = _render_field(key, value, read_only)

    return edited


def _render_field(key: str, value: Any, read_only: bool, prefix: str = "") -> Any:
    """Render a single configuration field."""
    full_key = f"{prefix}_{key}" if prefix else key

    if isinstance(value, bool):
        return st.checkbox(key, value=value, disabled=read_only, key=f"field_{full_key}")

    elif isinstance(value, int):
        return st.number_input(
            key,
            value=value,
            disabled=read_only,
            key=f"field_{full_key}",
            step=1,
        )

    elif isinstance(value, float):
        return st.number_input(
            key,
            value=value,
            disabled=read_only,
            key=f"field_{full_key}",
            format="%.6f",
        )

    elif isinstance(value, str):
        return st.text_input(key, value=value, disabled=read_only, key=f"field_{full_key}")

    elif isinstance(value, list):
        return _render_list_field(key, value, read_only, full_key)

    elif isinstance(value, dict):
        return _render_dict_field(key, value, read_only, full_key)

    else:
        # Fallback to text input with string conversion
        return st.text_input(
            key,
            value=str(value) if value is not None else "",
            disabled=read_only,
            key=f"field_{full_key}",
        )


def _render_list_field(key: str, value: list, read_only: bool, full_key: str) -> list:
    """Render a list configuration field."""
    st.markdown(f"**{key}**")

    if not value:
        st.text("(empty list)")
        return value

    # For simple lists, show as text area
    if all(isinstance(item, str) for item in value):
        items_str = "\n".join(value)
        edited_str = st.text_area(
            f"{key} (one item per line)",
            value=items_str,
            disabled=read_only,
            key=f"field_{full_key}",
            height=100,
        )
        return [item.strip() for item in edited_str.split("\n") if item.strip()]

    # For complex lists, show as YAML
    yaml_str = yaml.dump(value, default_flow_style=False)
    edited_yaml = st.text_area(
        f"{key} (YAML)",
        value=yaml_str,
        disabled=read_only,
        key=f"field_{full_key}",
        height=150,
    )

    try:
        return yaml.safe_load(edited_yaml) or []
    except yaml.YAMLError:
        return value


def _render_dict_field(key: str, value: dict, read_only: bool, full_key: str) -> dict:
    """Render a dictionary configuration field."""
    with st.expander(f"{key}", expanded=False):
        edited = {}
        for sub_key, sub_value in value.items():
            edited[sub_key] = _render_field(sub_key, sub_value, read_only, full_key)
        return edited


def render_config_list(
    configs: list,
    on_select: Optional[Callable[[str], None]] = None,
    selected: Optional[str] = None,
):
    """Render a list of configuration files.

    Args:
        configs: List of configuration info dictionaries
        on_select: Callback when a config is selected
        selected: Currently selected config name
    """
    if not configs:
        st.info("No configurations found.")
        return

    for config in configs:
        config_name = config.get("config_name", "Unknown")
        script_name = config.get("script_file_name", "Unknown")

        is_selected = config_name == selected

        col1, col2 = st.columns([3, 1])

        with col1:
            # Config info
            icon = ":page_facing_up:" if not is_selected else ":page_with_curl:"
            st.markdown(f"{icon} **{config_name}**")
            st.caption(f"Script: {script_name}")

        with col2:
            if st.button("Select", key=f"select_{config_name}", disabled=is_selected):
                if on_select:
                    on_select(config_name)

        st.markdown("---")


def render_new_config_form(
    on_create: Optional[Callable[[str, Dict], None]] = None,
    available_scripts: Optional[list] = None,
):
    """Render a form for creating new configurations.

    Args:
        on_create: Callback when create button is clicked
        available_scripts: List of available script names
    """
    st.subheader("Create New Configuration")

    # Config name
    config_name = st.text_input(
        "Configuration Name",
        placeholder="my-strategy-config",
        key="new_config_name",
    )

    # Script selection
    if available_scripts:
        script = st.selectbox(
            "Script",
            options=available_scripts,
            key="new_config_script",
        )
    else:
        script = st.text_input(
            "Script File Name",
            placeholder="gateway_lp_guarded.py",
            key="new_config_script_input",
        )

    # Initial YAML content
    default_yaml = """# Strategy Configuration
script_file_name: gateway_lp_guarded.py
controllers_config: []
candles_config: []
markets: {}
"""
    yaml_content = st.text_area(
        "Configuration (YAML)",
        value=default_yaml,
        height=300,
        key="new_config_yaml",
    )

    # Create button
    if st.button("Create Configuration", type="primary", key="create_config_btn"):
        if not config_name:
            st.error("Configuration name is required.")
            return

        try:
            config_data = yaml.safe_load(yaml_content) or {}
            if script:
                config_data["script_file_name"] = script

            if on_create:
                on_create(config_name, config_data)
                st.success(f"Configuration '{config_name}' created!")
                st.rerun()

        except yaml.YAMLError as e:
            st.error(f"Invalid YAML: {e}")
