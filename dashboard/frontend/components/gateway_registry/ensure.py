from typing import Dict, List, Optional

from frontend.components.gateway_registry.common import extract_network_value
from frontend.components.gateway_registry.validators import is_valid_solana_address


def find_token_match(tokens: List[Dict], token_input: str) -> Optional[Dict]:
    if not tokens or not token_input:
        return None
    value = token_input.strip()
    if not value:
        return None
    is_address = value.startswith("0x") or is_valid_solana_address(value)
    if is_address:
        value_lower = value.lower()
        for token in tokens:
            if not isinstance(token, dict):
                continue
            address = token.get("address")
            if address and str(address).lower() == value_lower:
                return token
        return None

    value_lower = value.lower()
    for token in tokens:
        if not isinstance(token, dict):
            continue
        symbol = token.get("symbol")
        if symbol and str(symbol).lower() == value_lower:
            return token
    return None


def pool_exists(existing_pools: List[Dict], pool_address: str) -> bool:
    if not pool_address:
        return False
    address_lower = str(pool_address).lower()
    for pool in existing_pools or []:
        if not isinstance(pool, dict):
            continue
        address = pool.get("address") or pool.get("pool_address")
        if address and str(address).lower() == address_lower:
            return True
    return False


def build_add_pool_payload(
    *,
    connector_name: str,
    network_id: str,
    pool_type: str,
    pool: Dict,
) -> Dict:
    network_value = extract_network_value(network_id or "")
    fee_value = pool.get("fee_pct")
    if fee_value is None:
        fee_value = pool.get("fee_tier")

    payload = {
        "connector_name": connector_name,
        "type": pool_type,
        "network": network_value,
        "address": pool.get("address") or pool.get("pool_address") or pool.get("id"),
        "base": pool.get("base_symbol") or pool.get("base"),
        "quote": pool.get("quote_symbol") or pool.get("quote"),
        "base_address": pool.get("base_address") or pool.get("base_token_address"),
        "quote_address": pool.get("quote_address") or pool.get("quote_token_address"),
    }
    if fee_value is not None:
        payload["fee_pct"] = float(fee_value)
    if pool.get("bin_step") is not None:
        payload["bin_step"] = pool.get("bin_step")
    return payload
