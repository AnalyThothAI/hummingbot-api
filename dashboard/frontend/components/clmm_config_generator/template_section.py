from typing import Dict, List, Optional, Tuple


def _st():
    import streamlit as st

    return st


def as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: object) -> bool:
    return bool(value)

FIELD_COMMON: List[Dict[str, object]] = [
    {"key": "position_value_quote", "type": "float", "min": 0.0, "step": 0.1},
    {"key": "position_width_pct", "type": "float", "min": 0.0, "step": 0.5},
    {"key": "rebalance_enabled", "type": "bool"},
    {"key": "rebalance_seconds", "type": "int", "min": 0, "step": 10},
    {"key": "stop_loss_pnl_pct", "type": "float", "min": 0.0, "step": 0.01, "format": "%.4f"},
    {"key": "reenter_enabled", "type": "bool"},
]

FIELD_ADVANCED: List[Dict[str, object]] = [
    {"key": "target_price", "type": "float", "step": 0.01},
    {"key": "trigger_above", "type": "bool"},
    {"key": "ratio_clamp_tick_multiplier", "type": "int", "min": 0, "step": 1},
    {"key": "ratio_edge_buffer_pct", "type": "float", "min": 0.0, "step": 0.01, "format": "%.4f"},
    {"key": "strategy_type", "type": "int", "min": 0, "step": 1},
    {"key": "hysteresis_pct", "type": "float", "min": 0.0, "step": 0.001, "format": "%.6f"},
    {"key": "cooldown_seconds", "type": "int", "min": 0, "step": 10},
    {"key": "max_rebalances_per_hour", "type": "int", "min": 0, "step": 1},
    {"key": "auto_swap_enabled", "type": "bool"},
    {"key": "swap_min_value_pct", "type": "float", "min": 0.0, "step": 0.001, "format": "%.6f"},
    {"key": "swap_safety_buffer_pct", "type": "float", "min": 0.0, "step": 0.001, "format": "%.6f"},
    {"key": "swap_slippage_pct", "type": "float", "min": 0.0, "step": 0.001, "format": "%.6f"},
    {"key": "inventory_drift_tolerance_pct", "type": "float", "min": 0.0, "step": 0.001, "format": "%.6f"},
    {"key": "normalization_cooldown_sec", "type": "int", "min": 0, "step": 10},
    {"key": "normalization_min_value_pct", "type": "float", "min": 0.0, "step": 0.001, "format": "%.6f"},
    {"key": "normalization_strict", "type": "bool"},
    {"key": "cost_filter_enabled", "type": "bool"},
    {"key": "cost_filter_fee_rate_bootstrap_quote_per_hour", "type": "float", "min": 0.0, "step": 0.1},
    {"key": "cost_filter_fixed_cost_quote", "type": "float", "min": 0.0, "step": 0.1},
    {"key": "cost_filter_max_payback_sec", "type": "int", "min": 0, "step": 60},
    {"key": "stop_loss_pause_sec", "type": "int", "min": 0, "step": 60},
    {"key": "native_token_symbol", "type": "text"},
    {"key": "min_native_balance", "type": "float", "min": 0.0, "step": 0.0001, "format": "%.6f"},
]


def render_param_fields(template_config: Dict, fields: List[Dict[str, object]], columns: int = 3) -> Dict:
    st = _st()
    values: Dict[str, object] = {}
    cols = st.columns(columns)
    col_idx = 0

    for field in fields:
        key = str(field["key"])
        if key not in template_config:
            continue
        col = cols[col_idx % columns]
        col_idx += 1
        field_type = field.get("type")
        label = key
        widget_key = f"gen_param_{key}"

        with col:
            if field_type == "bool":
                value = st.checkbox(label, value=as_bool(template_config.get(key)), key=widget_key)
            elif field_type == "int":
                value = st.number_input(
                    label,
                    min_value=int(field.get("min", 0)),
                    value=as_int(template_config.get(key), 0),
                    step=int(field.get("step", 1)),
                    key=widget_key,
                )
            elif field_type == "text":
                value = st.text_input(label, value=str(template_config.get(key, "")), key=widget_key)
            else:
                value = st.number_input(
                    label,
                    min_value=float(field.get("min", 0.0)),
                    value=as_float(template_config.get(key), 0.0),
                    step=float(field.get("step", 0.01)),
                    format=str(field.get("format", "%.4f")),
                    key=widget_key,
                )

        values[key] = value

    return values

def extract_defaults_from_template(template_fields: Dict) -> Dict:
    defaults = {}
    for key, meta in template_fields.items():
        if isinstance(meta, dict) and "default" in meta:
            defaults[key] = meta.get("default")
    return defaults


def select_template_defaults(config_data: Dict, template_fields: Dict) -> Dict:
    if isinstance(config_data, dict) and config_data:
        return config_data
    if isinstance(template_fields, dict) and template_fields:
        return extract_defaults_from_template(template_fields)
    return {}


def fetch_template_defaults(controller_type: str, controller_name: str, config_name: str) -> Dict:
    st = _st()
    from frontend.st_utils import backend_api_request

    response = backend_api_request(
        "GET",
        f"/controllers/configs/{config_name}",
        timeout=30,
    )
    config_data = response.get("data", {}) if response.get("ok") else {}

    response = backend_api_request(
        "GET",
        f"/controllers/{controller_type}/{controller_name}/config/template",
        timeout=30,
    )
    template_fields = response.get("data", {}) if response.get("ok") else {}

    defaults = select_template_defaults(config_data, template_fields)
    if not defaults:
        st.error("Failed to load template defaults from backend.")
        st.stop()
    return defaults


def _get_template_registry():
    return {
        "Uniswap CLMM": {
            "controller_type": "generic",
            "controller_name": "clmm_lp_uniswap",
            "config_name": "clmm_lp_uniswap",
        },
        "Meteora CLMM": {
            "controller_type": "generic",
            "controller_name": "clmm_lp_meteora",
            "config_name": "clmm_lp_meteora",
        },
    }


def render_template_section() -> Tuple[Dict, str]:
    st = _st()
    st.info("1) Select a standard template")
    registry = _get_template_registry()
    template_label = st.radio(
        "Template",
        options=list(registry.keys()),
        horizontal=True,
        key="gen_template_label",
    )
    template_meta = registry[template_label]
    template_id = template_meta["controller_name"]
    template_config = fetch_template_defaults(
        template_meta["controller_type"],
        template_meta["controller_name"],
        template_meta["config_name"],
    )
    template_config["id"] = template_id

    st.caption(f"Template ID: {template_id}")
    st.caption(f"Connector: {template_config.get('connector_name', '-')}")

    return template_config, template_id


def render_param_section(template_config: Dict) -> Tuple[Dict, bool]:
    st = _st()
    st.info("2) Strategy parameters")
    overrides: Dict[str, object] = {}
    overrides.update(render_param_fields(template_config, FIELD_COMMON))

    with st.expander("Advanced parameters", expanded=False):
        overrides.update(render_param_fields(template_config, FIELD_ADVANCED))

    budget_key_auto = st.checkbox("Use config id as budget_key", value=True)

    return overrides, budget_key_auto
