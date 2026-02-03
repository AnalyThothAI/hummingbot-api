import json
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.components.controller_config_generator_helpers import select_default_controller_type_index
from frontend.components.gateway_registry import render_gateway_pool_picker
from frontend.components.gateway_registry.common import is_gateway_connector
from frontend.st_utils import backend_api_request, get_backend_api_client


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def _normalize_id_value(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return normalized or "item"


def _collect_existing_ids() -> set:
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


def _fetch_controllers() -> Dict[str, List[str]]:
    response = backend_api_request("GET", "/controllers")
    if not response.get("ok"):
        st.error("Failed to load controllers from backend.")
        st.stop()
    payload = response.get("data", {})
    return payload if isinstance(payload, dict) else {}


def _fetch_schema(controller_type: str, controller_name: str) -> Dict[str, Any]:
    response = backend_api_request(
        "GET",
        f"/controllers/{controller_type}/{controller_name}/config/schema",
        timeout=30,
    )
    if not response.get("ok"):
        st.error("Failed to load controller schema from backend.")
        st.stop()
    payload = response.get("data", {})
    return payload if isinstance(payload, dict) else {}


def _resolve_schema_type(prop: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    if "enum" in prop:
        return "enum", prop
    if "type" in prop:
        return prop.get("type"), prop
    if "anyOf" in prop:
        for item in prop.get("anyOf", []):
            if isinstance(item, dict) and item.get("type") not in (None, "null"):
                if "enum" in item:
                    return "enum", item
                return item.get("type"), item
    return None, prop


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _stringify_list(values: List[Any]) -> str:
    return "\n".join(str(item) for item in values)


def _parse_array(text: str, item_type: Optional[str]) -> List[Any]:
    if not text:
        return []
    parts = [part.strip() for part in text.replace(",", "\n").splitlines() if part.strip()]
    parsed: List[Any] = []
    for part in parts:
        if item_type == "integer":
            try:
                parsed.append(int(float(part)))
                continue
            except (TypeError, ValueError):
                parsed.append(part)
                continue
        if item_type == "number":
            try:
                parsed.append(float(part))
                continue
            except (TypeError, ValueError):
                parsed.append(part)
                continue
        parsed.append(part)
    return parsed


def _render_field(
    *,
    name: str,
    prop: Dict[str, Any],
    default_value: Any,
    locked: bool,
    key_prefix: str,
) -> Any:
    field_type, resolved = _resolve_schema_type(prop)
    widget_key = f"{key_prefix}_{name}"
    label = name

    if field_type == "enum":
        options = resolved.get("enum") or []
        if not options:
            return st.text_input(label, value=str(default_value) if default_value is not None else "", key=widget_key, disabled=locked)
        selected = default_value if default_value in options else options[0]
        return st.selectbox(
            label,
            options=options,
            index=options.index(selected) if selected in options else 0,
            key=widget_key,
            disabled=locked,
        )

    if field_type == "boolean":
        return st.checkbox(label, value=_as_bool(default_value), key=widget_key, disabled=locked)

    if field_type == "integer":
        return st.number_input(
            label,
            value=_as_int(default_value, 0),
            step=1,
            key=widget_key,
            disabled=locked,
        )

    if field_type == "number":
        return st.number_input(
            label,
            value=_as_float(default_value, 0.0),
            step=0.001,
            format="%.6f",
            key=widget_key,
            disabled=locked,
        )

    if field_type == "array":
        item_type = None
        items = resolved.get("items", {})
        if isinstance(items, dict):
            item_type = items.get("type")
        display_value = default_value if isinstance(default_value, list) else []
        text_value = _stringify_list(display_value)
        raw = st.text_area(label, value=text_value, key=widget_key, disabled=locked, height=80)
        return _parse_array(raw, item_type)

    if field_type == "object":
        display_value = default_value if isinstance(default_value, (dict, list)) else {}
        text_value = json.dumps(display_value, indent=2)
        raw = st.text_area(label, value=text_value, key=widget_key, disabled=locked, height=120)
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return raw

    return st.text_input(label, value=str(default_value) if default_value is not None else "", key=widget_key, disabled=locked)


def _render_form(
    schema: Dict[str, Any],
    defaults: Dict[str, Any],
    locked_fields: set,
    hidden_fields: set,
    key_prefix: str,
) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    field_order = list(defaults.keys())
    for name in properties.keys():
        if name not in field_order:
            field_order.append(name)

    cols = st.columns(3)
    col_idx = 0
    for name in field_order:
        if name == "id" or name in hidden_fields:
            continue
        prop = properties.get(name, {})
        default_value = defaults.get(name)
        locked = name in locked_fields
        with cols[col_idx % 3]:
            values[name] = _render_field(
                name=name,
                prop=prop,
                default_value=default_value,
                locked=locked,
                key_prefix=key_prefix,
            )
        col_idx += 1
    return values


def render_config_generator_page() -> None:
    st.title("Config Generator")
    st.subheader("Generate controller configs from code templates")

    controllers = _fetch_controllers()
    controller_types = sorted(controllers.keys())
    if not controller_types:
        st.info("No controllers available.")
        return

    st.info("1) Select controller")
    with st.container(border=True):
        default_type_index = select_default_controller_type_index(controller_types)
        selected_type = st.selectbox(
            "Controller Type",
            controller_types,
            index=default_type_index,
            key="gen_controller_type",
        )
        controller_names = sorted(controllers.get(selected_type, []))
        if not controller_names:
            st.info("No controllers available for the selected type.")
            return

        selected_name = st.selectbox("Controller", controller_names, key="gen_controller_name")
    key_prefix = _normalize_key(f"gen_{selected_type}_{selected_name}")

    schema_payload = _fetch_schema(selected_type, selected_name)
    schema = schema_payload.get("schema", {})
    defaults = schema_payload.get("defaults", {})
    meta = schema_payload.get("meta", {})
    if not isinstance(defaults, dict):
        defaults = {}

    defaults["controller_type"] = selected_type
    defaults["controller_name"] = selected_name

    connector_name = str(defaults.get("connector_name") or "")
    is_gateway = is_gateway_connector(connector_name)
    clmm_mode = is_gateway and "pool_address" in defaults

    locked_fields = {"controller_type", "controller_name"}
    if clmm_mode and "router_connector" in defaults:
        locked_fields.update({"connector_name", "router_connector"})

    st.caption(f"Template: {selected_type}.{selected_name}")
    fixed_fields = [f"controller_type={selected_type}", f"controller_name={selected_name}"]
    if connector_name:
        fixed_fields.append(f"connector_name={connector_name}")
    router_connector = defaults.get("router_connector")
    if router_connector:
        fixed_fields.append(f"router_connector={router_connector}")
    st.caption("Fixed fields: " + ", ".join(fixed_fields))

    hidden_fields = {"controller_type", "controller_name"}
    if clmm_mode:
        hidden_fields.update({"connector_name", "router_connector"})

    with st.container(border=True):
        st.info("2) Configure parameters")
        param_values = _render_form(schema, defaults, locked_fields, hidden_fields, key_prefix)

    base_config = dict(defaults)
    base_config.update(param_values)

    existing_ids = _collect_existing_ids()
    reserved_ids = set(existing_ids)

    pool_rows: List[Dict[str, Optional[str]]] = []
    override_rows: List[Dict[str, Optional[str]]] = []

    if clmm_mode:
        st.divider()
        st.info("3) Select pools (CLMM)")
        pool_rows_key = f"{key_prefix}_pool_rows"
        render_gateway_pool_picker(
            prefix=f"{key_prefix}_pool",
            connector_name=connector_name,
            allow_connector_override=False,
            target_rows_key=pool_rows_key,
            show_existing_toggle=True,
            show_filters=False,
        )
        pool_rows = st.session_state.get(pool_rows_key, [])

        with st.expander("Advanced: manual overrides", expanded=False):
            st.caption("Format: TRADING_PAIR[,POOL_TRADING_PAIR,POOL_ADDRESS]")
            raw_overrides = st.text_area(
                "Trading Pair Overrides",
                key=f"{key_prefix}_overrides",
                height=160,
                placeholder="SOL-USDC\nSOL-USDC, SOL-USDC, pool_address",
            )
            override_rows = generator.parse_override_rows(raw_overrides)

        override_rows = generator.merge_override_rows(pool_rows, override_rows, prefer_new=True)
        if not override_rows:
            st.caption("Select pools or enter at least one trading pair to continue.")
            st.stop()
    else:
        st.divider()
        st.info("3) Define target configs")
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        batch_candidates = [
            name for name in properties.keys()
            if name not in locked_fields and name != "id"
        ]
        field_options = ["(none)"] + sorted(batch_candidates)
        default_batch_field = "trading_pair" if "trading_pair" in batch_candidates else "(none)"
        st.caption("Select a batch field for multiple configs, or choose (none) for a single config.")
        batch_field = st.selectbox(
            "Batch Field",
            options=field_options,
            index=field_options.index(default_batch_field) if default_batch_field in field_options else 0,
            key=f"{key_prefix}_batch_field",
        )

        override_rows = []
        if batch_field == "(none)":
            config_id = st.text_input("Config ID", value=selected_name, key=f"{key_prefix}_config_id")
            if not config_id:
                st.caption("Enter a config id to continue.")
                st.stop()
            override_rows.append({"config_id": config_id, "overrides": {}})
        else:
            raw_values = st.text_area(
                "Batch Values (one per line)",
                key=f"{key_prefix}_batch_values",
                height=160,
                placeholder="BTC-USDT\nETH-USDT",
            )
            values = [line.strip() for line in raw_values.splitlines() if line.strip()]
            if not values:
                st.caption("Enter at least one value to generate configs.")
                st.stop()
            field_prop = properties.get(batch_field, {})
            field_type, _ = _resolve_schema_type(field_prop) if isinstance(field_prop, dict) else (None, {})
            for value in values:
                parsed_value: Any = value
                if field_type == "integer":
                    parsed_value = _as_int(value)
                elif field_type == "number":
                    parsed_value = _as_float(value)
                elif field_type == "boolean":
                    parsed_value = _as_bool(value)
                override_rows.append({"batch_value": value, "overrides": {batch_field: parsed_value}})

    budget_key_auto = False
    if "budget_key" in base_config:
        budget_key_auto = st.checkbox("Use config id as budget_key", value=True, key=f"{key_prefix}_budget_key_auto")

    st.divider()
    st.info("4) Preview & generate")

    plans_ready: List[Dict[str, Any]] = []
    plan_rows: List[Dict[str, Any]] = []

    if clmm_mode:
        for row in override_rows:
            errors = generator.validate_override_row(base_config, row)
            trading_pair = row.get("trading_pair") or ""
            normalized = _normalize_id_value(trading_pair)
            candidate = f"{selected_name}--{normalized}"
            new_id = candidate
            suffix = 1
            while new_id in reserved_ids:
                new_id = f"{candidate}-{suffix}"
                suffix += 1
            reserved_ids.add(new_id)

            payload = generator.build_override_payload(base_config, row)
            payload["id"] = new_id
            if budget_key_auto:
                payload["budget_key"] = new_id

            status = "Ready" if not errors else "Error"
            plan_rows.append({
                "New Config": new_id,
                "Trading Pair": payload.get("trading_pair", "-"),
                "Pool Trading Pair": payload.get("pool_trading_pair", "-"),
                "Pool Address": payload.get("pool_address", "-"),
                "Status": status,
                "Notes": "; ".join(errors) if errors else "-",
            })

            if not errors:
                plans_ready.append({"config_id": new_id, "payload": payload})
    else:
        for row in override_rows:
            overrides = row.get("overrides", {})
            batch_value = str(row.get("batch_value", ""))
            if "config_id" in row:
                new_id = row["config_id"]
            else:
                normalized = _normalize_id_value(batch_value)
                candidate = f"{selected_name}--{normalized}"
                new_id = candidate
                suffix = 1
                while new_id in reserved_ids:
                    new_id = f"{candidate}-{suffix}"
                    suffix += 1
                reserved_ids.add(new_id)

            errors = []
            if "config_id" in row and new_id in existing_ids:
                errors.append("config id already exists")
            elif "config_id" in row:
                reserved_ids.add(new_id)

            payload = dict(base_config)
            payload.update(overrides)
            payload["id"] = new_id
            if budget_key_auto:
                payload["budget_key"] = new_id

            status = "Ready" if not errors else "Error"
            plan_rows.append({
                "New Config": new_id,
                "Field": list(overrides.keys())[0] if overrides else "-",
                "Value": batch_value if overrides else "-",
                "Status": status,
                "Notes": "; ".join(errors) if errors else "-",
            })

            if not errors:
                plans_ready.append({"config_id": new_id, "payload": payload})

    if plan_rows:
        st.dataframe(pd.DataFrame(plan_rows), use_container_width=True, hide_index=True)

    if not plans_ready:
        st.warning("Fix errors above before generating configs.")
        st.stop()

    st.success(f"{len(plans_ready)} config(s) ready to generate")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Generate Configs", type="primary", use_container_width=True):
            created = []
            failures = []
            for plan in plans_ready:
                config_id = plan["config_id"]
                payload = plan["payload"]

                validate_response = backend_api_request(
                    "POST",
                    f"/controllers/{selected_type}/{selected_name}/config/validate",
                    json_body=payload,
                )
                if not validate_response.get("ok"):
                    failures.append({
                        "config_id": config_id,
                        "error": validate_response.get("data", {}).get("detail") or validate_response.get("error"),
                    })
                    continue

                response = backend_api_request(
                    "POST",
                    f"/controllers/configs/{config_id}",
                    json_body=payload,
                )
                if response.get("ok"):
                    created.append(config_id)
                else:
                    failures.append({
                        "config_id": config_id,
                        "error": response.get("error", "Failed to create config."),
                    })

            if created:
                st.success(f"Created {len(created)} configs.")
            if failures:
                st.error("Some configs failed to create.")
                st.dataframe(pd.DataFrame(failures), use_container_width=True, hide_index=True)

            if created:
                st.rerun()

    with col2:
        if st.button("Go to Deploy V2", use_container_width=True):
            st.switch_page("frontend/pages/orchestration/launch_bot_v2/app.py")
