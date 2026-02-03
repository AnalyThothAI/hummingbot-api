from typing import Optional, Tuple


def is_gateway_connector(connector_name: str) -> bool:
    return isinstance(connector_name, str) and "/" in connector_name


def connector_base_name(connector_name: str) -> str:
    if not connector_name:
        return ""
    return connector_name.split("/", 1)[0]


def connector_pool_type(connector_name: str) -> Optional[str]:
    if not connector_name or "/" not in connector_name:
        return None
    _, suffix = connector_name.split("/", 1)
    suffix = suffix.strip().lower()
    return suffix if suffix in {"clmm", "amm"} else None


def split_network_id(network_id: str) -> Tuple[str, str]:
    if not network_id:
        return "", ""
    if "-" in network_id:
        chain, network = network_id.split("-", 1)
        return chain, network
    return network_id, ""


def extract_network_value(network_id: str) -> Optional[str]:
    if not network_id:
        return None
    if "-" in network_id:
        _, network_value = network_id.split("-", 1)
        return network_value
    return network_id
