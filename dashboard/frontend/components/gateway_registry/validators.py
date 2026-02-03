import re
from typing import Optional, Tuple


def normalize_evm_address(address: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        from eth_utils import is_address, is_checksum_address, to_checksum_address
    except Exception:
        return None, "eth_utils not available"

    if not is_address(address):
        return None, "Invalid EVM address."
    checksum = to_checksum_address(address)
    if not is_checksum_address(address):
        return checksum, f"Checksum address applied: {checksum}"
    return checksum, None


def is_valid_solana_address(address: str) -> bool:
    return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address))
