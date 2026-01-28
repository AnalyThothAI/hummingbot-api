"""Workflow planning utilities for MCP adapter."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from mcp.http_client import McpHttpClient, McpHttpError


def build_deploy_v2_workflow_plan(arguments: dict, http_client: McpHttpClient) -> Dict[str, Any]:
    """Build a read-only workflow plan for deploy-v2 flows."""
    plan: Dict[str, Any] = {
        "summary": {},
        "checks": [],
        "actions": [],
        "blockers": [],
        "notes": [],
    }

    network_id = _get_str(arguments, "network_id")
    network = _get_str(arguments, "network")
    if not network and network_id:
        network = _derive_network_from_network_id(network_id)

    connector_name = _normalize_connector_name(_get_str(arguments, "connector_name"))
    pool_type = _get_str(arguments, "pool_type")
    pool_address = _get_str(arguments, "pool_address")
    base = _get_str(arguments, "base")
    quote = _get_str(arguments, "quote")
    base_address = _get_str(arguments, "base_address")
    quote_address = _get_str(arguments, "quote_address")
    fee_pct = arguments.get("fee_pct")

    tokens = _normalize_tokens(arguments.get("tokens") or [])
    wallet_address = _get_str(arguments, "wallet_address")
    spender = _get_str(arguments, "spender")
    approval_amount = _get_str(arguments, "approval_amount")

    deployment_type = _get_str(arguments, "deployment_type") or "controllers"
    instance_name = _get_str(arguments, "instance_name")
    credentials_profile = _get_str(arguments, "credentials_profile")
    image = _get_str(arguments, "image")
    headless = arguments.get("headless")
    gateway_network_id = _get_str(arguments, "gateway_network_id")
    gateway_wallet_address = _get_str(arguments, "gateway_wallet_address")

    script = _get_str(arguments, "script")
    script_config = _get_str(arguments, "script_config")
    controllers_config = arguments.get("controllers_config") or []

    gateway_passphrase = _get_str(arguments, "gateway_passphrase")
    gateway_image = _get_str(arguments, "gateway_image")
    gateway_port = arguments.get("gateway_port")
    gateway_dev_mode = arguments.get("gateway_dev_mode")

    # Check Gateway status
    gateway_running = None
    try:
        status = http_client.get("/gateway/status")
        gateway_running = bool(status.get("running")) if isinstance(status, dict) else None
        plan["checks"].append({"name": "gateway_status", "status": "ok", "details": status})
    except McpHttpError as exc:
        plan["checks"].append({"name": "gateway_status", "status": "error", "details": f"HTTP {exc.status_code}"})
    except Exception as exc:  # pragma: no cover - defensive
        plan["checks"].append({"name": "gateway_status", "status": "error", "details": str(exc)})

    if gateway_running is False:
        plan["blockers"].append("gateway_not_running")
        if gateway_passphrase:
            action_payload = _pick_params(
                {
                    "passphrase": gateway_passphrase,
                    "image": gateway_image,
                    "port": gateway_port,
                    "dev_mode": gateway_dev_mode,
                },
                ["passphrase", "image", "port", "dev_mode"],
            )
            plan["actions"].append({
                "tool": "gateway_start",
                "arguments": action_payload,
                "reason": "gateway_not_running",
            })
        else:
            plan["notes"].append("gateway_start requires gateway_passphrase")

    # Tokens check (with metadata autofill when needed)
    if network_id and tokens:
        tokens = _fill_missing_token_metadata(network_id, tokens, http_client, plan)
        token_status = _check_tokens(network_id, tokens, http_client, plan)
        if token_status.get("missing"):
            for token in token_status["missing"]:
                plan["actions"].append({
                    "tool": "gateway_token_add",
                    "arguments": {
                        "network_id": network_id,
                        "address": token.get("address"),
                        "symbol": token.get("symbol"),
                        "name": token.get("name"),
                        "decimals": token.get("decimals"),
                    },
                    "reason": "token_missing",
                })
    elif tokens and not network_id:
        plan["blockers"].append("network_id_required_for_tokens")
    elif not tokens:
        plan["notes"].append("tokens not provided; skipping gateway token checks")

    # Pools check
    if connector_name:
        if not pool_address and tokens:
            pool_address = _maybe_resolve_pool_address(
                network_id,
                connector_name,
                pool_type,
                tokens,
                http_client,
                plan,
            )
            if pool_address:
                plan["notes"].append("pool_address resolved via metadata_pools")
        pool_status = _check_pools(
            connector_name,
            network,
            pool_address,
            base,
            quote,
            base_address,
            quote_address,
            http_client,
            plan,
        )
        if pool_status.get("missing") is True:
            pool_payload = _pick_params(
                {
                    "connector_name": connector_name,
                    "type": pool_type,
                    "network": network,
                    "address": pool_address,
                    "base": base,
                    "quote": quote,
                    "base_address": base_address,
                    "quote_address": quote_address,
                    "fee_pct": fee_pct,
                },
                [
                    "connector_name",
                    "type",
                    "network",
                    "address",
                    "base",
                    "quote",
                    "base_address",
                    "quote_address",
                    "fee_pct",
                ],
            )
            missing_pool_inputs = [
                key
                for key in ("type", "network", "address", "base", "quote", "base_address", "quote_address")
                if key not in pool_payload
            ]
            if missing_pool_inputs:
                plan["blockers"].append("pool_details_missing")
                plan["notes"].append(f"pool add requires: {', '.join(missing_pool_inputs)}")
            else:
                plan["actions"].append({
                    "tool": "gateway_pool_add",
                    "arguments": pool_payload,
                    "reason": "pool_missing",
                })
        elif pool_status.get("missing") is None:
            plan["notes"].append("pool status unknown; manual check recommended")
    else:
        plan["blockers"].append("connector_name_required")

    # Allowances check
    if network_id and wallet_address and spender and tokens:
        allowance_status = _check_allowances(network_id, wallet_address, spender, tokens, http_client, plan)
        for token_symbol in allowance_status.get("missing", []):
            plan["actions"].append({
                "tool": "gateway_approve",
                "arguments": {
                    "network_id": network_id,
                    "address": wallet_address,
                    "token": token_symbol,
                    "spender": spender,
                    "amount": approval_amount,
                },
                "reason": "allowance_missing",
            })
    else:
        if not network_id:
            plan["notes"].append("allowance check requires network_id")
        if not wallet_address:
            plan["notes"].append("allowance check requires wallet_address")
        if not spender:
            plan["notes"].append("allowance check requires spender")
        if not tokens:
            plan["notes"].append("allowance check requires tokens")

    # Config existence check
    if deployment_type == "script":
        if script_config:
            if not _config_exists("/scripts/configs", script_config, http_client, plan, "script_config"):
                plan["actions"].append({
                    "tool": "script_config_upsert",
                    "arguments": {
                        "config_name": script_config,
                        "config": {},
                    },
                    "reason": "script_config_missing",
                    "note": "fill config payload before execute",
                })
        else:
            plan["blockers"].append("script_config_required")
        if not script:
            plan["blockers"].append("script_required")
    else:
        if not controllers_config:
            plan["blockers"].append("controllers_config_required")
        else:
            for config_name in controllers_config:
                if not _config_exists("/controllers/configs", config_name, http_client, plan, "controller_config"):
                    plan["actions"].append({
                        "tool": "controller_config_upsert",
                        "arguments": {
                            "config_name": config_name,
                            "config": {},
                        },
                        "reason": "controller_config_missing",
                        "note": "fill config payload before execute",
                    })

    # Instance existence check
    if instance_name:
        exists, instance_info = _instance_exists(instance_name, http_client, plan)
        if exists:
            plan["notes"].append(f"instance '{instance_name}' already exists")
        else:
            deploy_tool, deploy_args = _build_deploy_action(
                deployment_type,
                instance_name,
                credentials_profile,
                image,
                headless,
                gateway_network_id,
                gateway_wallet_address,
                script,
                script_config,
                controllers_config,
            )
            if deploy_tool:
                plan["actions"].append({
                    "tool": deploy_tool,
                    "arguments": deploy_args,
                    "reason": "instance_missing",
                })
    else:
        plan["blockers"].append("instance_name_required")

    if _needs_gateway_restart(plan):
        plan["actions"].append({
            "tool": "gateway_restart",
            "arguments": {},
            "reason": "gateway_restart_required",
        })
        plan["notes"].append("Gateway restart required after adding tokens/pools.")

    plan["summary"] = _build_summary(plan)
    return plan


def _check_tokens(network_id: str, tokens: List[dict], http_client: McpHttpClient, plan: Dict[str, Any]) -> Dict[str, Any]:
    missing = []
    found = []
    try:
        result = http_client.get(f"/gateway/networks/{network_id}/tokens")
        token_list = result.get("tokens", []) if isinstance(result, dict) else []
        for token in tokens:
            if _token_in_list(token, token_list):
                found.append(token)
            else:
                missing.append(token)
        plan["checks"].append({
            "name": "gateway_tokens",
            "status": "ok",
            "details": {
                "found": _token_ids(found),
                "missing": _token_ids(missing),
            },
        })
    except Exception as exc:
        plan["checks"].append({"name": "gateway_tokens", "status": "error", "details": str(exc)})
    return {"found": found, "missing": missing}


def _fill_missing_token_metadata(
    network_id: str,
    tokens: List[dict],
    http_client: McpHttpClient,
    plan: Dict[str, Any],
) -> List[dict]:
    enriched = []
    for token in tokens:
        if not isinstance(token, dict):
            continue
        address = token.get("address")
        if not address:
            enriched.append(token)
            continue
        needs_symbol = not token.get("symbol")
        needs_decimals = token.get("decimals") is None
        needs_name = not token.get("name")
        if not (needs_symbol or needs_decimals or needs_name):
            enriched.append(token)
            continue
        try:
            result = http_client.get("/metadata/token", params={"network_id": network_id, "address": address})
            payload = result.get("token", {}) if isinstance(result, dict) else {}
            token_copy = dict(token)
            if needs_symbol and payload.get("symbol"):
                token_copy["symbol"] = payload.get("symbol")
            if needs_decimals and payload.get("decimals") is not None:
                token_copy["decimals"] = payload.get("decimals")
            if needs_name and payload.get("name"):
                token_copy["name"] = payload.get("name")
            enriched.append(token_copy)
            plan["checks"].append({
                "name": "metadata_token",
                "status": "ok",
                "details": {"address": address, "token": payload},
            })
        except Exception as exc:
            enriched.append(token)
            plan["checks"].append({
                "name": "metadata_token",
                "status": "error",
                "details": {"address": address, "error": str(exc)},
            })
    return enriched


def _check_pools(
    connector_name: str,
    network: Optional[str],
    pool_address: Optional[str],
    base: Optional[str],
    quote: Optional[str],
    base_address: Optional[str],
    quote_address: Optional[str],
    http_client: McpHttpClient,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    params = {"connector_name": connector_name}
    if network:
        params["network"] = network
    missing: Optional[bool] = None
    matched_pool = None
    try:
        pools = http_client.get("/gateway/pools", params=params)
        if isinstance(pools, list):
            missing = True
            for pool in pools:
                if pool_address and _match_pool_address(pool, pool_address):
                    matched_pool = pool
                    missing = False
                    break
                if not pool_address and _match_pool_symbols(
                    pool,
                    base,
                    quote,
                    base_address,
                    quote_address,
                    allow_reverse=_allow_reverse_pair(connector_name),
                ):
                    matched_pool = pool
                    missing = False
                    break
        plan["checks"].append({
            "name": "gateway_pools",
            "status": "ok",
            "details": {
                "found": bool(matched_pool),
                "matched_pool": matched_pool,
            },
        })
    except Exception as exc:
        plan["checks"].append({"name": "gateway_pools", "status": "error", "details": str(exc)})
    return {"missing": missing, "matched_pool": matched_pool}


def _check_allowances(
    network_id: str,
    wallet_address: str,
    spender: str,
    tokens: List[dict],
    http_client: McpHttpClient,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    token_values = [_token_symbol_or_address(token) for token in tokens if _token_symbol_or_address(token)]
    missing = []
    try:
        result = http_client.post(
            "/gateway/allowances",
            json_body={
                "network_id": network_id,
                "address": wallet_address,
                "tokens": token_values,
                "spender": spender,
            },
        )
        allowance_map = _parse_allowances(result)
        if allowance_map:
            for token_value in token_values:
                allowance = allowance_map.get(token_value)
                if allowance is None or _is_zeroish(allowance):
                    missing.append(token_value)
            plan["checks"].append({
                "name": "gateway_allowances",
                "status": "ok",
                "details": {
                    "missing": missing,
                    "allowances": allowance_map,
                },
            })
        else:
            plan["checks"].append({
                "name": "gateway_allowances",
                "status": "unknown",
                "details": result,
            })
    except Exception as exc:
        plan["checks"].append({"name": "gateway_allowances", "status": "error", "details": str(exc)})
    return {"missing": missing}


def _config_exists(
    base_path: str,
    config_name: str,
    http_client: McpHttpClient,
    plan: Dict[str, Any],
    check_name: str,
) -> bool:
    try:
        http_client.get(f"{base_path}/{config_name}")
        plan["checks"].append({"name": check_name, "status": "ok", "details": {"config_name": config_name}})
        return True
    except McpHttpError as exc:
        if exc.status_code == 404:
            plan["checks"].append({"name": check_name, "status": "missing", "details": {"config_name": config_name}})
            return False
        plan["checks"].append({"name": check_name, "status": "error", "details": f"HTTP {exc.status_code}"})
        return False
    except Exception as exc:
        plan["checks"].append({"name": check_name, "status": "error", "details": str(exc)})
        return False


def _instance_exists(instance_name: str, http_client: McpHttpClient, plan: Dict[str, Any]) -> Tuple[bool, Optional[dict]]:
    try:
        result = http_client.get("/bot-orchestration/instances")
        instances = []
        if isinstance(result, dict):
            instances = (result.get("data") or {}).get("instances", [])
        match = next((item for item in instances if item.get("name") == instance_name), None)
        plan["checks"].append({
            "name": "instances",
            "status": "ok",
            "details": {"instance_name": instance_name, "exists": bool(match)},
        })
        return bool(match), match
    except Exception as exc:
        plan["checks"].append({"name": "instances", "status": "error", "details": str(exc)})
        return False, None


def _build_deploy_action(
    deployment_type: str,
    instance_name: str,
    credentials_profile: Optional[str],
    image: Optional[str],
    headless: Optional[bool],
    gateway_network_id: Optional[str],
    gateway_wallet_address: Optional[str],
    script: Optional[str],
    script_config: Optional[str],
    controllers_config: List[str],
) -> Tuple[Optional[str], Dict[str, Any]]:
    if not credentials_profile:
        return None, {}

    if deployment_type == "script":
        payload = _pick_params(
            {
                "instance_name": instance_name,
                "credentials_profile": credentials_profile,
                "image": image,
                "script": script,
                "script_config": script_config,
                "gateway_network_id": gateway_network_id,
                "gateway_wallet_address": gateway_wallet_address,
                "headless": headless,
            },
            [
                "instance_name",
                "credentials_profile",
                "image",
                "script",
                "script_config",
                "gateway_network_id",
                "gateway_wallet_address",
                "headless",
            ],
        )
        return "bot_deploy_v2_script", payload

    payload = _pick_params(
        {
            "instance_name": instance_name,
            "credentials_profile": credentials_profile,
            "controllers_config": controllers_config,
            "image": image,
            "gateway_network_id": gateway_network_id,
            "gateway_wallet_address": gateway_wallet_address,
            "headless": headless,
        },
        [
            "instance_name",
            "credentials_profile",
            "controllers_config",
            "image",
            "gateway_network_id",
            "gateway_wallet_address",
            "headless",
        ],
    )
    return "bot_deploy_v2_controllers", payload


def _build_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    blockers = plan.get("blockers", [])
    actions = plan.get("actions", [])
    return {
        "ready": not blockers,
        "blockers": blockers,
        "action_count": len(actions),
    }


def _token_in_list(token: dict, token_list: List[dict]) -> bool:
    address = _safe_lower(token.get("address"))
    symbol = _safe_lower(token.get("symbol"))
    for item in token_list:
        item_address = _safe_lower(item.get("address"))
        item_symbol = _safe_lower(item.get("symbol"))
        if address and item_address and address == item_address:
            return True
        if symbol and item_symbol and symbol == item_symbol:
            return True
    return False


def _token_symbol_or_address(token: dict) -> Optional[str]:
    return token.get("symbol") or token.get("address")


def _token_ids(tokens: List[dict]) -> List[dict]:
    return [
        {
            "symbol": token.get("symbol"),
            "address": token.get("address"),
        }
        for token in tokens
    ]


def _match_pool_address(pool: dict, pool_address: str) -> bool:
    address = _safe_lower(pool_address)
    pool_addr = _safe_lower(pool.get("address"))
    return bool(address and pool_addr and address == pool_addr)


def _match_pool_symbols(
    pool: dict,
    base: Optional[str],
    quote: Optional[str],
    base_address: Optional[str],
    quote_address: Optional[str],
    allow_reverse: bool,
) -> bool:
    if not base and not base_address:
        return False
    pool_base = _safe_lower(pool.get("base"))
    pool_quote = _safe_lower(pool.get("quote"))
    pool_base_addr = _safe_lower(pool.get("base_address"))
    pool_quote_addr = _safe_lower(pool.get("quote_address"))

    if base_address and pool_base_addr and base_address.lower() == pool_base_addr:
        if quote_address and pool_quote_addr:
            return quote_address.lower() == pool_quote_addr
        return True
    if allow_reverse and base_address and quote_address and pool_base_addr and pool_quote_addr:
        if base_address.lower() == pool_quote_addr and quote_address.lower() == pool_base_addr:
            return True

    if base and pool_base and base.lower() == pool_base:
        if quote and pool_quote:
            return quote.lower() == pool_quote
        return True
    if allow_reverse and base and quote and pool_base and pool_quote:
        if base.lower() == pool_quote and quote.lower() == pool_base:
            return True

    return False


def _parse_allowances(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        if "allowances" in result and isinstance(result["allowances"], dict):
            return result["allowances"]
        if all(isinstance(value, (int, float, str)) for value in result.values()):
            return result
    if isinstance(result, list):
        allowance_map = {}
        for item in result:
            if not isinstance(item, dict):
                continue
            token = item.get("token") or item.get("symbol") or item.get("address")
            allowance = item.get("allowance") or item.get("amount")
            if token is not None:
                allowance_map[token] = allowance
        if allowance_map:
            return allowance_map
    return {}


def _is_zeroish(value: Any) -> bool:
    try:
        return float(value) <= 0
    except (TypeError, ValueError):
        return False


def _get_str(arguments: dict, key: str) -> Optional[str]:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _safe_lower(value: Any) -> Optional[str]:
    if not value:
        return None
    return str(value).lower()


def _normalize_connector_name(connector_name: Optional[str]) -> Optional[str]:
    if not connector_name:
        return None
    if "/" in connector_name:
        return connector_name.split("/", 1)[0]
    return connector_name


def _allow_reverse_pair(connector_name: Optional[str]) -> bool:
    return connector_name == "uniswap"


def _normalize_tokens(tokens: List[dict]) -> List[dict]:
    normalized = []
    for token in tokens:
        if not isinstance(token, dict):
            continue
        normalized.append(token)
    return normalized


def _maybe_resolve_pool_address(
    network_id: Optional[str],
    connector_name: Optional[str],
    pool_type: Optional[str],
    tokens: List[dict],
    http_client: McpHttpClient,
    plan: Dict[str, Any],
) -> Optional[str]:
    if not network_id:
        return None
    if not connector_name:
        return None
    token_a = _token_symbol_or_address(tokens[0]) if len(tokens) > 0 else None
    token_b = _token_symbol_or_address(tokens[1]) if len(tokens) > 1 else None
    params = _pick_params(
        {
            "network_id": network_id,
            "connector": connector_name,
            "pool_type": pool_type,
            "token_a": token_a,
            "token_b": token_b,
            "pages": 1,
            "limit": 50,
        },
        ["network_id", "connector", "pool_type", "token_a", "token_b", "pages", "limit"],
    )
    try:
        result = http_client.get("/metadata/pools", params=params)
        pools = result.get("pools", []) if isinstance(result, dict) else []
        if pools:
            plan["checks"].append({
                "name": "metadata_pools",
                "status": "ok",
                "details": {"count": len(pools)},
            })
            return pools[0].get("address")
        plan["checks"].append({"name": "metadata_pools", "status": "ok", "details": {"count": 0}})
    except Exception as exc:
        plan["checks"].append({"name": "metadata_pools", "status": "error", "details": str(exc)})
    return None


def _needs_gateway_restart(plan: Dict[str, Any]) -> bool:
    for action in plan.get("actions", []):
        if action.get("tool") in {"gateway_token_add", "gateway_pool_add"}:
            return True
    return False


def _derive_network_from_network_id(network_id: str) -> Optional[str]:
    if not network_id or "-" not in network_id:
        return None
    return network_id.split("-", 1)[1]


def _pick_params(arguments: dict, keys: Iterable[str]) -> dict:
    payload = {}
    for key in keys:
        if key in arguments and arguments[key] is not None:
            payload[key] = arguments[key]
    return payload
