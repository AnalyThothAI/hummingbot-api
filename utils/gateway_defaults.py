from typing import Optional


def should_apply_gateway_defaults(
    apply_gateway_defaults: bool,
    gateway_network_id: Optional[str],
    gateway_wallet_address: Optional[str],
) -> bool:
    return bool(apply_gateway_defaults and (gateway_network_id or gateway_wallet_address))
