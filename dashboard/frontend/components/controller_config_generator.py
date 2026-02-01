import csv
from io import StringIO
from typing import Dict, List, Optional


def parse_override_rows(raw_text: str) -> List[Dict[str, Optional[str]]]:
    rows: List[Dict[str, Optional[str]]] = []
    if not raw_text:
        return rows

    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        reader = csv.reader(StringIO(stripped))
        values = next(reader, [])
        values = [value.strip() for value in values]

        trading_pair = values[0] if len(values) >= 1 and values[0] else None
        pool_trading_pair = values[1] if len(values) >= 2 and values[1] else None
        pool_address = values[2] if len(values) >= 3 and values[2] else None

        rows.append({
            "line_no": line_no,
            "raw": line,
            "trading_pair": trading_pair,
            "pool_trading_pair": pool_trading_pair,
            "pool_address": pool_address,
        })

    return rows


def is_valid_trading_pair(value: Optional[str]) -> bool:
    if not value or "-" not in value:
        return False
    base, quote = value.split("-", 1)
    return bool(base.strip() and quote.strip())


def _needs_pool_trading_pair(base_config: Dict) -> bool:
    connector_name = str(base_config.get("connector_name") or "")
    if "pool_trading_pair" in base_config:
        return True
    return "/clmm" in connector_name or "/amm" in connector_name


def validate_override_row(base_config: Dict, row: Dict[str, Optional[str]]) -> List[str]:
    errors: List[str] = []
    trading_pair = row.get("trading_pair")

    if not is_valid_trading_pair(trading_pair):
        errors.append("Invalid trading_pair")
        return errors

    if "pool_address" in base_config:
        base_pair = base_config.get("trading_pair")
        if trading_pair != base_pair and not row.get("pool_address"):
            errors.append("pool_address required when trading_pair changes")

    return errors


def build_override_payload(base_config: Dict, row: Dict[str, Optional[str]]) -> Dict:
    payload = dict(base_config)
    trading_pair = row.get("trading_pair")
    if trading_pair:
        payload["trading_pair"] = trading_pair

    if _needs_pool_trading_pair(base_config):
        pool_pair = row.get("pool_trading_pair") or trading_pair
        if pool_pair:
            payload["pool_trading_pair"] = pool_pair

    if "pool_address" in base_config:
        pool_address = row.get("pool_address")
        if pool_address:
            payload["pool_address"] = pool_address

    return payload
