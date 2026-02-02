import csv
from decimal import Decimal
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


def pool_to_override_row(pool: Dict) -> Dict[str, Optional[str]]:
    trading_pair = pool.get("trading_pair")
    if not trading_pair:
        base = pool.get("base")
        quote = pool.get("quote")
        if base and quote:
            trading_pair = f"{base}-{quote}"

    pool_address = pool.get("address") or pool.get("pool_address") or pool.get("id")

    return {
        "trading_pair": trading_pair or None,
        "pool_trading_pair": trading_pair or None,
        "pool_address": pool_address or None,
    }


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


def _row_key(row: Dict[str, Optional[str]]) -> str:
    pair = row.get("pool_trading_pair") or row.get("trading_pair") or ""
    pair = pair.strip().upper()
    if pair:
        return f"pair:{pair}"
    address = row.get("pool_address")
    if address:
        return f"addr:{str(address).lower()}"
    line_no = row.get("line_no")
    if line_no is not None:
        return f"line:{line_no}"
    return f"row:{id(row)}"


def merge_override_rows(
    existing: List[Dict[str, Optional[str]]],
    new: List[Dict[str, Optional[str]]],
    prefer_new: bool = False,
) -> List[Dict[str, Optional[str]]]:
    merged = list(existing)
    key_to_index = {_row_key(row): idx for idx, row in enumerate(merged)}

    for row in new:
        key = _row_key(row)
        if key in key_to_index:
            if prefer_new:
                index = key_to_index[key]
                merged[index] = row
            continue
        key_to_index[key] = len(merged)
        merged.append(row)

    return merged


def apply_param_overrides(base_config: Dict, overrides: Dict[str, Optional[object]]) -> Dict:
    payload = dict(base_config)
    for key, value in overrides.items():
        if value is None:
            continue
        payload[key] = value
    return payload


def compute_param_overrides(base_config: Dict, values: Dict[str, Optional[object]]) -> Dict:
    overrides: Dict[str, Optional[object]] = {}

    def _normalize(value: object):
        if isinstance(value, bool):
            return "bool", value
        if isinstance(value, (int, float, Decimal)):
            return "num", float(value)
        if isinstance(value, str):
            try:
                return "num", float(value)
            except ValueError:
                return "str", value
        return "other", value

    for key, value in values.items():
        if value is None:
            continue
        if key not in base_config:
            overrides[key] = value
            continue
        base_value = base_config.get(key)
        left_tag, left_val = _normalize(base_value)
        right_tag, right_val = _normalize(value)
        if left_tag == right_tag == "num":
            if abs(left_val - right_val) > 1e-12:
                overrides[key] = value
            continue
        if left_val != right_val:
            overrides[key] = value

    return overrides


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
