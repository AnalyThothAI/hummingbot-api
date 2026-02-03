import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from frontend.components import controller_config_generator as generator
from frontend.components.controller_config_generator_helpers import select_default_controller_type_index
from frontend.components.gateway_registry.common import (
    connector_base_name,
    connector_pool_type,
    extract_network_value,
    is_gateway_connector,
)
from frontend.components.gateway_registry.ensure import build_add_pool_payload, find_token_match, pool_exists
from frontend.components.gateway_registry.validators import (
    is_valid_solana_address,
    normalize_evm_address,
)
from frontend.components.gateway_registry.normalizers import normalize_existing_pool, normalize_search_pool
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

# Gateway helpers
def _get_gateway_networks(prefix: str) -> List[Dict]:
    state_key = f"{prefix}_gateway_networks"
    if state_key in st.session_state:
        return st.session_state[state_key]
    response = backend_api_request("GET", "/gateway/networks")
    networks = response.get("data", {}).get("networks", []) if response.get("ok") else []
    networks = [item for item in networks if isinstance(item, dict) and item.get("network_id")]
    st.session_state[state_key] = networks
    return networks


def _get_gateway_tokens(prefix: str, network_id: str, force_refresh: bool = False) -> List[Dict]:
    state_key = f"{prefix}_gateway_tokens:{network_id}"
    if not force_refresh and state_key in st.session_state:
        return st.session_state[state_key]
    response = backend_api_request("GET", f"/gateway/networks/{network_id}/tokens")
    payload = response.get("data", {})
    tokens = payload.get("tokens", payload if isinstance(payload, list) else [])
    tokens = [item for item in tokens if isinstance(item, dict)]
    st.session_state[state_key] = tokens
    return tokens


def _get_gateway_pools(
    prefix: str,
    connector_name: str,
    network_value: str,
    pool_type: str,
    search_term: str,
    force_refresh: bool = False,
) -> List[Dict]:
    cache_key = f"{prefix}_gateway_pools:{connector_name}:{network_value}:{pool_type}:{search_term}"
    if not force_refresh and cache_key in st.session_state:
        return st.session_state[cache_key]
    response = backend_api_request(
        "GET",
        "/gateway/pools",
        params={
            "connector_name": connector_name,
            "network": network_value,
            "pool_type": pool_type,
            "search": search_term or None,
        },
    )
    pools = response.get("data", []) if response.get("ok") else []
    pools = [item for item in pools if isinstance(item, dict)]
    st.session_state[cache_key] = pools
    return pools


def _pick_default_symbol(options: List[str], candidates: List[str]) -> Optional[str]:
    option_map = {opt.lower(): opt for opt in options}
    for candidate in candidates:
        value = option_map.get(candidate.lower())
        if value:
            return value
    return options[0] if options else None


def _default_token_pair(network_id: str, token_options: List[str]) -> Tuple[Optional[str], Optional[str]]:
    network_lower = (network_id or "").lower()
    base_candidates = []
    quote_candidates = []
    if "bsc" in network_lower or "binance" in network_lower:
        base_candidates = ["BNB", "WBNB"]
        quote_candidates = ["USDT", "USDC", "BUSD"]
    elif "base" in network_lower:
        base_candidates = ["ETH", "WETH"]
        quote_candidates = ["USDC", "USDT"]
    elif "sol" in network_lower:
        base_candidates = ["SOL"]
        quote_candidates = ["USDC", "USDT"]
    else:
        base_candidates = ["ETH", "WETH", "SOL", "BNB"]
        quote_candidates = ["USDC", "USDT"]

    base = _pick_default_symbol(token_options, base_candidates)
    quote = _pick_default_symbol(token_options, quote_candidates)
    if base == quote and len(token_options) > 1:
        for option in token_options:
            if option != base:
                quote = option
                break
    return base, quote


def _quote_symbols_for_network(network_id: str) -> Tuple[List[str], List[str]]:
    network_lower = (network_id or "").lower()
    if "bsc" in network_lower or "binance" in network_lower:
        return ["USDT"], ["BNB", "WBNB"]
    if "base" in network_lower:
        return ["USDC"], ["ETH", "WETH"]
    if "sol" in network_lower:
        return ["USDC"], ["SOL", "WSOL"]
    return ["USDC"], ["ETH", "WETH"]


WRAPPED_SYMBOL_MAP = {
    "WSOL": "SOL",
    "WETH": "ETH",
    "WBNB": "BNB",
}

WRAPPED_ADDRESS_MAP = {
    "so11111111111111111111111111111111111111112": "SOL",
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "BNB",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "ETH",
}


def _canonical_symbol(symbol: Optional[str], address: Optional[str] = None) -> str:
    addr = (address or "").lower()
    if addr in WRAPPED_ADDRESS_MAP:
        return WRAPPED_ADDRESS_MAP[addr]
    sym = (symbol or "").upper()
    if sym in WRAPPED_SYMBOL_MAP:
        return WRAPPED_SYMBOL_MAP[sym]
    return sym


def _filter_pools_by_tokens(
    pools: List[Dict],
    base_symbol: str,
    quote_symbol: str,
    base_address: Optional[str],
    quote_address: Optional[str],
) -> List[Dict]:
    if not base_symbol or not quote_symbol:
        return []
    base_addr = (base_address or "").lower()
    quote_addr = (quote_address or "").lower()
    base_canon = _canonical_symbol(base_symbol, base_address)
    quote_canon = _canonical_symbol(quote_symbol, quote_address)
    base_lower = base_canon.lower()
    quote_lower = quote_canon.lower()

    filtered = []
    for pool in pools:
        p_base_addr = str(pool.get("base_address") or "").lower()
        p_quote_addr = str(pool.get("quote_address") or "").lower()
        if base_addr and quote_addr and p_base_addr and p_quote_addr:
            if {p_base_addr, p_quote_addr} == {base_addr, quote_addr}:
                filtered.append(pool)
                continue

        p_base_symbol = _canonical_symbol(pool.get("base_symbol"), pool.get("base_address")).lower()
        p_quote_symbol = _canonical_symbol(pool.get("quote_symbol"), pool.get("quote_address")).lower()
        if {p_base_symbol, p_quote_symbol} == {base_lower, quote_lower}:
            filtered.append(pool)

    return filtered



def _format_api_error(response: Dict, fallback: str) -> str:
    data = response.get("data", {})
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return detail
    return response.get("error") or fallback


def _maybe_add_token(prefix: str, network_id: str, token_input: str) -> Optional[Dict]:
    if not token_input or not network_id:
        return None
    token_value = token_input.strip()
    if not token_value:
        return None

    tokens = _get_gateway_tokens(prefix, network_id)
    match = find_token_match(tokens, token_value)
    if match:
        return match

    lookup_address = None
    checksum_notice = None
    if token_value.startswith("0x"):
        checksum_address, checksum_notice = normalize_evm_address(token_value)
        if checksum_address is None:
            st.error("Invalid EVM address. Check the address format.")
            return None
        lookup_address = checksum_address
    elif is_valid_solana_address(token_value):
        lookup_address = token_value
    else:
        st.info("Token symbol not found in Gateway. Use address to auto-add.")
        return None

    response = backend_api_request(
        "GET",
        "/metadata/token",
        params={
            "network_id": network_id,
            "address": lookup_address,
        },
    )
    if not response.get("ok"):
        st.error(_format_api_error(response, "Failed to fetch token metadata."))
        return None

    payload = response.get("data", {})
    token = payload.get("token", {}) if isinstance(payload, dict) else {}
    if not isinstance(token, dict):
        st.error("Token metadata not available.")
        return None

    add_payload = {
        "address": lookup_address,
        "symbol": token.get("symbol"),
        "decimals": token.get("decimals"),
    }
    if token.get("name"):
        add_payload["name"] = token.get("name")

    add_response = backend_api_request(
        "POST",
        f"/gateway/networks/{network_id}/tokens",
        json_body=add_payload,
    )
    if not add_response.get("ok"):
        st.error(_format_api_error(add_response, "Failed to add token."))
        return None

    message = add_response.get("data", {}).get(
        "message",
        "Token added. Restart Gateway for changes to take effect.",
    )
    st.success(message)
    if checksum_notice:
        st.info(checksum_notice)

    tokens.append({
        "address": lookup_address,
        "symbol": token.get("symbol"),
        "decimals": token.get("decimals"),
        "name": token.get("name"),
    })
    st.session_state[f"{prefix}_gateway_tokens:{network_id}"] = tokens
    return tokens[-1]


def _maybe_add_pool(
    *,
    prefix: str,
    connector_name: str,
    network_id: str,
    pool_type: str,
    pool: Dict,
) -> bool:
    pool_address = pool.get("address") or pool.get("pool_address")
    if not pool_address:
        st.error("Selected pool has no address.")
        return False

    network_value = extract_network_value(network_id or "")
    existing_pools = _get_gateway_pools(
        prefix,
        connector_name,
        network_value or "",
        pool_type,
        search_term="",
        force_refresh=False,
    )
    if pool_exists(existing_pools, pool_address):
        return True

    payload = build_add_pool_payload(
        connector_name=connector_name,
        network_id=network_id,
        pool_type=pool_type,
        pool=pool,
    )
    response = backend_api_request("POST", "/gateway/pools", json_body=payload)
    if not response.get("ok"):
        st.error(_format_api_error(response, "Failed to add pool."))
        return False

    message = response.get("data", {}).get("message", "Pool added.")
    st.success(message)
    return True




def _resolve_token_for_search(tokens: List[Dict], token_input: str) -> Optional[str]:
    if not token_input:
        return None
    token_value = token_input.strip()
    if not token_value:
        return None
    match = find_token_match(tokens, token_value)
    if match and match.get("address"):
        return match.get("address")
    if token_value.startswith("0x"):
        checksum_address, _ = normalize_evm_address(token_value)
        return checksum_address or token_value
    if is_valid_solana_address(token_value):
        return token_value
    return token_value


def _lookup_token_metadata(prefix: str, network_id: str, address: str) -> Optional[Dict]:
    if not address or not network_id:
        return None
    cache_key = f"{prefix}_token_meta:{network_id}:{address}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    response = backend_api_request(
        "GET",
        "/metadata/token",
        params={
            "network_id": network_id,
            "address": address,
        },
    )
    if not response.get("ok"):
        st.session_state[cache_key] = None
        return None
    payload = response.get("data", {})
    token = payload.get("token", {}) if isinstance(payload, dict) else {}
    if not isinstance(token, dict):
        st.session_state[cache_key] = None
        return None
    st.session_state[cache_key] = token
    return token


def _fixed_quote_symbol(network_id: str, token_options: List[str]) -> Optional[str]:
    network_lower = (network_id or "").lower()
    if "bsc" in network_lower or "binance" in network_lower:
        candidates = ["USDT"]
    elif "base" in network_lower:
        candidates = ["USDC"]
    elif "sol" in network_lower:
        candidates = ["USDC"]
    else:
        candidates = ["USDC"]
    return _pick_default_symbol(token_options, candidates)



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


def _group_fields(field_order: List[str]) -> List[Tuple[str, List[str]]]:
    core = {
        "trading_pair",
        "target_price",
        "trigger_above",
        "position_value_quote",
        "position_width_pct",
        "ratio_edge_buffer_pct",
        "ratio_clamp_tick_multiplier",
        "strategy_type",
    }
    budget = {
        "budget_key",
        "native_token_symbol",
        "min_native_balance",
        "balance_update_timeout_sec",
        "balance_refresh_timeout_sec",
    }
    groups = {
        "Core": [],
        "Rebalance": [],
        "Exit": [],
        "Budget/Balance": [],
        "Other": [],
    }

    for name in field_order:
        if name in core or name.startswith("ratio_"):
            groups["Core"].append(name)
        elif name.startswith("rebalance_") or name in {"hysteresis_pct", "cooldown_seconds", "max_rebalances_per_hour"}:
            groups["Rebalance"].append(name)
        elif name.startswith("stop_loss") or name.startswith("take_profit") or name.startswith("exit_") or name in {"reenter_enabled"}:
            groups["Exit"].append(name)
        elif name in budget:
            groups["Budget/Balance"].append(name)
        else:
            groups["Other"].append(name)

    ordered = []
    for key in ["Core", "Rebalance", "Exit", "Budget/Balance", "Other"]:
        if groups[key]:
            ordered.append((key, groups[key]))
    return ordered


def _stringify_preview_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=True)
        except TypeError:
            return str(value)
    if value is None:
        return "-"
    return str(value)


def _build_preview_table(defaults: Dict[str, Any], payload: Dict[str, Any], diff_only: bool) -> pd.DataFrame:
    diff_keys = set(generator.compute_param_overrides(defaults, payload).keys())
    keys = sorted(set(defaults.keys()) | set(payload.keys()))
    rows = []
    for key in keys:
        if diff_only and key not in diff_keys:
            continue
        default_value = defaults.get(key, "-")
        current_value = payload.get(key, "-")
        status = "changed" if key in diff_keys else "default"
        rows.append({
            "Field": key,
            "Default": _stringify_preview_value(default_value),
            "Current": _stringify_preview_value(current_value),
            "Status": status,
        })
    return pd.DataFrame(rows)


def _style_preview_table(df: pd.DataFrame):
    def _row_style(row):
        if row.get("Status") == "changed":
            return ["background-color: #e7f6ea"] * len(row)
        return ["" for _ in row]
    return df.style.apply(_row_style, axis=1)


def _render_grouped_form(
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

    field_order = [name for name in field_order if name != "id" and name not in hidden_fields]
    grouped = _group_fields(field_order)

    for group_name, names in grouped:
        with st.expander(group_name, expanded=(group_name == "Core")):
            cols = st.columns(3)
            col_idx = 0
            for name in names:
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
    st.caption("Generate controller configs from code templates")

    controllers = _fetch_controllers()
    controller_types = sorted(controllers.keys())
    if not controller_types:
        st.info("No controllers available.")
        return

    main_col, preview_col = st.columns([2, 1], gap="large")

    selected_type = ""
    selected_name = ""
    base_config: Dict[str, Any] = {}
    plan_rows: List[Dict[str, Any]] = []
    plans_ready: List[Dict[str, Any]] = []
    preview_payloads: List[Dict[str, Any]] = []

    with main_col:
        st.markdown("**Template**")
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
        router_connector = defaults.get("router_connector")
        pool_mode = "pool_address" in defaults
        is_gateway = is_gateway_connector(connector_name) if connector_name else False

        fixed_fields = [f"controller_type={selected_type}", f"controller_name={selected_name}"]
        if connector_name:
            fixed_fields.append(f"connector_name={connector_name}")
        if router_connector:
            fixed_fields.append(f"router_connector={router_connector}")
        st.caption("Fixed fields: " + ", ".join(fixed_fields))

        hidden_fields = {"controller_type", "controller_name"}
        hidden_fields.update({
            name for name, info in meta.items()
            if isinstance(info, dict) and info.get("hidden") is True
        })
        if pool_mode:
            hidden_fields.update({"connector_name", "router_connector", "trading_pair", "pool_address"})

        locked_fields = {"controller_type", "controller_name"}
        if pool_mode and "router_connector" in defaults:
            locked_fields.update({"connector_name", "router_connector"})

        override_rows_key = f"{key_prefix}_override_rows"
        param_values_key = f"{key_prefix}_param_values"
        budget_key_auto_key = f"{key_prefix}_budget_key_auto"
        needs_pool_trading_pair = "pool_trading_pair" in defaults

        if pool_mode:
            st.markdown("**Pools (Gateway)**")
            with st.container(border=True):
                if not connector_name:
                    st.warning("Connector name is missing in the template.")
                elif not is_gateway:
                    st.error("Pool strategies require a gateway connector. Update the template connector.")
                if not connector_name or not is_gateway:
                    st.caption("Gateway pool selection is disabled until a valid gateway connector is set.")
                else:
                    networks = _get_gateway_networks(key_prefix)
                    network_ids = sorted({item.get("network_id") for item in networks if item.get("network_id")})
                    network_options = ["(select network)"] + network_ids
                    selected_network_id = st.selectbox(
                        "Gateway Network",
                        options=network_options,
                        index=0,
                        key=f"{key_prefix}_network",
                    )

                    tokens: List[Dict] = []
                    token_options: List[str] = []
                    token_map: Dict[str, Dict] = {}
                    if selected_network_id != "(select network)":
                        tokens = _get_gateway_tokens(key_prefix, selected_network_id)
                        for token in tokens:
                            symbol = token.get("symbol") or token.get("token")
                            if not symbol:
                                continue
                            symbol = str(symbol)
                            token_map.setdefault(symbol.lower(), token)
                        token_options = sorted({sym for sym in [t.get("symbol") for t in tokens if isinstance(t, dict)] if sym})

                    if not token_options:
                        st.info("Select a network to load available tokens.")
                        base_symbol = None
                        quote_symbol = None
                        base_input = None
                        quote_choice = "Stable"
                    else:
                        base_symbol, _ = _default_token_pair(selected_network_id, token_options)
                        if base_symbol is None:
                            base_symbol = token_options[0]

                        stable_candidates, native_candidates = _quote_symbols_for_network(selected_network_id)
                        quote_choice = st.radio(
                            "Quote",
                            options=["Stable", "Native"],
                            horizontal=True,
                            key=f"{key_prefix}_quote_choice",
                        )
                        if quote_choice == "Stable":
                            quote_symbol = _pick_default_symbol(token_options, stable_candidates)
                            if quote_symbol is None and stable_candidates:
                                quote_symbol = stable_candidates[0]
                        else:
                            quote_symbol = _pick_default_symbol(token_options, native_candidates)
                            if quote_symbol is None and native_candidates:
                                quote_symbol = native_candidates[0]
                        if quote_symbol is None and token_options:
                            quote_symbol = token_options[0]

                        cols = st.columns([2, 2])
                        with cols[0]:
                            base_symbol = st.selectbox(
                                "Base Token",
                                options=token_options,
                                index=token_options.index(base_symbol) if base_symbol in token_options else 0,
                                key=f"{key_prefix}_base_token",
                            )
                        with cols[1]:
                            st.text_input(
                                "Quote Token (fixed)",
                                value=quote_symbol or "",
                                disabled=True,
                                key=f"{key_prefix}_quote_token_fixed",
                            )

                        base_override = st.text_input(
                            "Base token override (symbol or address)",
                            key=f"{key_prefix}_base_override",
                            placeholder="e.g. SOL or 0x...",
                        )
                        base_input = base_override.strip() if base_override else base_symbol

                        base_match = find_token_match(tokens, base_input) if base_input else None
                        base_address = None
                        if base_match:
                            base_address = base_match.get("address")
                            symbol = base_match.get("symbol") or base_match.get("token") or "token"
                            st.info(f"{symbol} found in Gateway.")
                        else:
                            if base_input and (base_input.startswith("0x") or is_valid_solana_address(base_input)):
                                if base_input.startswith("0x"):
                                    base_address, _ = normalize_evm_address(base_input)
                                else:
                                    base_address = base_input
                                if base_address:
                                    meta = _lookup_token_metadata(key_prefix, selected_network_id, base_address)
                                    if meta:
                                        st.caption(f"Detected token: {meta.get('symbol')} ({meta.get('decimals')} decimals)")
                                    st.caption("Token not in Gateway. You can add it.")
                                    if st.button(
                                        "Add Base Token to Gateway",
                                        key=f"{key_prefix}_add_base_btn",
                                        use_container_width=True,
                                    ):
                                        _maybe_add_token(key_prefix, selected_network_id, base_address)
                                        st.rerun()
                            elif base_input:
                                st.caption("Token symbol not found in Gateway. Use address to import.")

                        if base_symbol == quote_symbol:
                            st.warning("Base and quote tokens are identical.")


                    pool_rows = st.session_state.get(override_rows_key, [])
                    if token_options and base_symbol and quote_symbol:
                        st.caption("Existing Gateway pools are shown first. If none match, we search the network.")
                        search_term = st.text_input("Filter pools (optional)", key=f"{key_prefix}_pool_filter")
                        connector_base = connector_base_name(connector_name)
                        pool_type = connector_pool_type(connector_name) or "clmm"
                        network_value = extract_network_value(selected_network_id or "") or ""

                        refresh = st.button("Refresh Pools", key=f"{key_prefix}_pool_refresh", use_container_width=True)
                        query_key = f"{key_prefix}_pool_query"
                        current_query = (
                            selected_network_id,
                            base_input or base_symbol,
                            quote_symbol,
                            search_term,
                            pool_type,
                        )
                        last_query = st.session_state.get(query_key)
                        now = time.time()
                        last_fetch = st.session_state.get(f"{key_prefix}_pool_last_fetch", 0.0)
                        should_refresh = current_query != last_query
                        if should_refresh and (now - last_fetch) < 0.6:
                            should_refresh = False
                        if should_refresh:
                            st.session_state[query_key] = current_query
                            st.session_state[f"{key_prefix}_pool_last_fetch"] = now

                        pools_raw = _get_gateway_pools(
                            key_prefix,
                            connector_base,
                            network_value,
                            pool_type,
                            "",
                            force_refresh=refresh or should_refresh,
                        )
                        normalized_existing = [normalize_existing_pool(pool) for pool in pools_raw if isinstance(pool, dict)]

                        base_token = token_map.get(base_symbol.lower()) if token_map else None
                        quote_token = token_map.get(quote_symbol.lower()) if token_map else None
                        base_addr = base_token.get("address") if isinstance(base_token, dict) else None
                        quote_addr = quote_token.get("address") if isinstance(quote_token, dict) else None

                        filtered_pools = _filter_pools_by_tokens(normalized_existing, base_symbol, quote_symbol, base_addr, quote_addr)
                        if search_term:
                            search_lower = search_term.lower()
                            filtered_pools = [
                                pool for pool in filtered_pools
                                if search_lower in str(pool.get("trading_pair") or "").lower()
                                or search_lower in str(pool.get("address") or "").lower()
                            ]

                        pools_to_show = filtered_pools
                        source_label = "Gateway Pools"

                        if not pools_to_show:
                            st.info("No Gateway pools found for this token pair.")
                            search_clicked = st.button("Search on network", key=f"{key_prefix}_pool_search", use_container_width=True)
                            cached_results = st.session_state.get(f"{key_prefix}_metadata_results", [])
                            if search_clicked:
                                token_a = _resolve_token_for_search(tokens, base_input or base_symbol)
                                token_b = _resolve_token_for_search(tokens, quote_symbol)
                                if token_a and token_b:
                                    with st.spinner("Searching pools on network..."):
                                        response = backend_api_request(
                                            "GET",
                                            "/metadata/pools",
                                            params={
                                                "connector": connector_base,
                                                "network_id": selected_network_id,
                                                "pool_type": pool_type,
                                                "token_a": token_a,
                                                "token_b": token_b,
                                                "search": search_term or None,
                                                "pages": 1,
                                                "limit": 50,
                                            },
                                        )
                                    if response.get("ok"):
                                        payload = response.get("data", {})
                                        search_results = payload.get("pools", []) if isinstance(payload, dict) else []
                                        cached_results = [
                                            normalize_search_pool(pool)
                                            for pool in search_results
                                            if isinstance(pool, dict)
                                        ]
                                        st.session_state[f"{key_prefix}_metadata_results"] = cached_results
                                else:
                                    st.warning("Base/quote token not resolved for search.")
                            if cached_results:
                                pools_to_show = cached_results
                                source_label = "Search Results (not in Gateway)"

                        if not pools_to_show:
                            st.info("No pools found for the selected token pair.")
                        else:
                            st.markdown(f"**{source_label}**")
                            display_rows = []
                            for pool in pools_to_show:
                                display_rows.append({
                                    "Pair": pool.get("trading_pair") or f"{base_symbol}-{quote_symbol}",
                                    "Fee %": pool.get("fee_tier"),
                                    "Base Address": pool.get("base_address"),
                                    "Quote Address": pool.get("quote_address"),
                                    "Address": pool.get("address"),
                                })
                            st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

                            options = {}
                            for pool in pools_to_show:
                                address = pool.get("address", "")
                                pair = pool.get("trading_pair") or f"{base_symbol}-{quote_symbol}"
                                label = " | ".join(part for part in [pair, address] if part)
                                options[label] = pool

                            selected_label = st.selectbox(
                                "Select Pool",
                                list(options.keys()),
                                key=f"{key_prefix}_pool_choice",
                            )
                            selected_pool = options.get(selected_label, {})

                            add_cols = st.columns(2)
                            with add_cols[0]:
                                if st.button("Add Pool to Config", key=f"{key_prefix}_pool_add", use_container_width=True):
                                    trading_pair = selected_pool.get("trading_pair") or f"{base_symbol}-{quote_symbol}"
                                    pool_address = selected_pool.get("address") or selected_pool.get("pool_address") or selected_pool.get("id")
                                    if pool_address:
                                        row = {
                                            "trading_pair": trading_pair,
                                            "pool_trading_pair": None,
                                            "pool_address": pool_address,
                                        }
                                        pool_rows = generator.merge_override_rows(pool_rows, [row], prefer_new=False)
                                        st.session_state[override_rows_key] = pool_rows
                            with add_cols[1]:
                                if source_label == "Search Results (not in Gateway)":
                                    if st.button("Add Pool to Gateway", key=f"{key_prefix}_pool_add_gateway", use_container_width=True):
                                        if selected_pool:
                                            added = _maybe_add_pool(
                                                prefix=key_prefix,
                                                connector_name=connector_base,
                                                network_id=selected_network_id,
                                                pool_type=pool_type,
                                                pool=selected_pool,
                                            )
                                            if added:
                                                st.rerun()
                                else:
                                    st.caption("Pool already in Gateway.")

                    if pool_rows:
                        st.markdown("**Selected Pools**")
                        st.dataframe(
                            pd.DataFrame(pool_rows),
                            use_container_width=True,
                            hide_index=True,
                        )
                        if st.button("Clear Selected Pools", key=f"{key_prefix}_pool_clear", use_container_width=True):
                            st.session_state[override_rows_key] = []
                            pool_rows = []
                            st.rerun()

                    with st.expander("Advanced: manual override", expanded=False):
                        st.caption("Allow manual overrides for trading_pair and pool_address.")
                        enable_manual = st.checkbox("Enable manual override", key=f"{key_prefix}_manual_enable")
                        if enable_manual:
                            default_pair = None
                            if base_symbol and quote_symbol:
                                default_pair = f"{base_symbol}-{quote_symbol}"
                            manual_pair = st.text_input("Trading Pair", value=default_pair or "", key=f"{key_prefix}_manual_pair")
                            manual_address = st.text_input("Pool Address", key=f"{key_prefix}_manual_address")
                            if st.button("Add Manual Pool", key=f"{key_prefix}_manual_add", use_container_width=True):
                                if manual_pair and manual_address:
                                    row = {
                                        "trading_pair": manual_pair,
                                        "pool_trading_pair": None,
                                        "pool_address": manual_address,
                                    }
                                    pool_rows = generator.merge_override_rows(pool_rows, [row], prefer_new=True)
                                    st.session_state[override_rows_key] = pool_rows
                                else:
                                    st.warning("Trading pair and pool address are required.")

        st.markdown("**Parameters**")
        with st.container(border=True):
            param_values = _render_grouped_form(schema, defaults, locked_fields, hidden_fields, key_prefix)
            st.session_state[param_values_key] = param_values

        if not pool_mode:
            st.markdown("**Targets**")
            with st.container(border=True):
                properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
                batch_candidates = [
                    name for name in properties.keys()
                    if name not in locked_fields and name != "id" and name not in hidden_fields
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
                    if config_id:
                        override_rows.append({"config_id": config_id, "overrides": {}})
                    else:
                        st.caption("Enter a config id to continue.")
                else:
                    raw_values = st.text_area(
                        "Batch Values (one per line)",
                        key=f"{key_prefix}_batch_values",
                        height=160,
                        placeholder="BTC-USDT\\nETH-USDT",
                    )
                    values = [line.strip() for line in raw_values.splitlines() if line.strip()]
                    if not values:
                        st.caption("Enter at least one value to generate configs.")
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

                if override_rows:
                    st.session_state[override_rows_key] = override_rows

        base_config = dict(defaults)
        base_config.update(st.session_state.get(param_values_key, {}))

        override_rows = st.session_state.get(override_rows_key, [])
        budget_key_auto = st.session_state.get(budget_key_auto_key, True)
        if "budget_key" in base_config:
            budget_key_auto = st.checkbox(
                "Use config id as budget_key",
                value=budget_key_auto,
                key=budget_key_auto_key,
            )

        if override_rows:
            existing_ids = _collect_existing_ids()
            reserved_ids = set(existing_ids)

            if pool_mode:
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
                    plan_row = {
                        "New Config": new_id,
                        "Trading Pair": payload.get("trading_pair", "-"),
                        "Pool Address": payload.get("pool_address", "-"),
                        "Status": status,
                        "Notes": "; ".join(errors) if errors else "-",
                    }
                    if "pool_trading_pair" in payload:
                        plan_row["Pool Trading Pair"] = payload.get("pool_trading_pair", "-")
                    plan_rows.append(plan_row)

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

        if plans_ready:
            preview_payloads = [plan["payload"] for plan in plans_ready]
        elif base_config:
            preview_payloads = [base_config]

        st.markdown("**Preview & Generate**")
        with st.container(border=True):
            if plan_rows:
                st.dataframe(pd.DataFrame(plan_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No preview rows yet.")

            if not plans_ready:
                st.warning("Fix errors above before generating configs.")
            else:
                st.success(f"{len(plans_ready)} config(s) ready to generate")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("Generate Configs", type="primary", use_container_width=True, disabled=not plans_ready):
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
                                "error": validate_response.get("data", {}).get("detail")
                                or validate_response.get("error"),
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

    with preview_col:
        st.subheader("Preview")
        if plan_rows:
            ready_count = len(plans_ready)
            st.caption(f"{ready_count} ready / {len(plan_rows)} total")
        if not preview_payloads:
            st.info("Complete template selection to see a preview.")
            return

        label_options = []
        label_to_payload = {}
        for idx, payload in enumerate(preview_payloads, start=1):
            label = payload.get("id") or f"draft-{idx}"
            label_options.append(label)
            label_to_payload[label] = payload

        selected_label = label_options[0]
        if len(label_options) > 1:
            selected_label = st.selectbox("Config Preview", label_options, key=f"{key_prefix}_preview_select")

        selected_payload = label_to_payload.get(selected_label, {})
        diff_only = st.checkbox("Show changes only", value=True, key=f"{key_prefix}_preview_diff")
        table = _build_preview_table(defaults, selected_payload, diff_only)
        if table.empty:
            st.info("No changes detected.")
        else:
            styled = _style_preview_table(table)
            st.dataframe(styled, use_container_width=True, hide_index=True)
