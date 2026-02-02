from typing import Dict, List, Optional, Set

import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.components.clmm_config_generator.pools_section import render_pool_section
from frontend.components.clmm_config_generator.preview_section import render_manual_overrides, render_preview_and_generate
from frontend.components.clmm_config_generator.template_section import render_param_section, render_template_section
from frontend.st_utils import get_backend_api_client


def _collect_existing_ids() -> Set[str]:
    backend_api_client = get_backend_api_client()
    try:
        controller_configs = backend_api_client.controllers.list_controller_configs()
    except Exception as e:
        st.error(f"Failed to fetch controller configs: {e}")
        st.stop()
    existing_ids = set()
    for config in controller_configs:
        if not isinstance(config, dict):
            continue
        config_id = config.get("id") or config.get("config", {}).get("id")
        if config_id:
            existing_ids.add(config_id)
    return existing_ids


def render_config_generator_page() -> None:
    st.title("Config Generator")
    st.subheader("Generate controller configs from standard CLMM templates")

    with st.container(border=True):
        template_config, template_id = render_template_section()

    if st.session_state.get("gen_template_id") != template_id:
        st.session_state["gen_template_id"] = template_id
        st.session_state.pop("gen_pool_rows", None)
        st.session_state.pop("gen_pool_results", None)
        st.session_state.pop("config_generator_overrides", None)

    existing_ids = _collect_existing_ids()

    st.divider()

    with st.container(border=True):
        param_overrides, budget_key_auto = render_param_section(template_config)

    st.divider()

    pool_rows = render_pool_section(template_config.get("connector_name", ""))

    manual_rows: List[Dict[str, Optional[str]]] = []
    with st.expander("Advanced: manual overrides", expanded=False):
        manual_rows = render_manual_overrides()

    override_rows = generator.merge_override_rows(pool_rows, manual_rows, prefer_new=True)
    if not override_rows:
        st.caption("Select pools or enter at least one trading pair to generate configs.")
        st.stop()

    st.divider()

    param_overrides = generator.compute_param_overrides(template_config, param_overrides)
    base_with_params = generator.apply_param_overrides(template_config, param_overrides)

    render_preview_and_generate(
        template_id=template_id,
        base_config=base_with_params,
        override_rows=override_rows,
        existing_ids=existing_ids,
        budget_key_auto=budget_key_auto,
    )
