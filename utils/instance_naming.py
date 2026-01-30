import re
import secrets
from datetime import datetime
from typing import Tuple

_INSTANCE_TIMESTAMP_RE = re.compile(r"\d{8}-\d{4}(\d{2})?")
_INSTANCE_SUFFIX_RE = re.compile(r"-[0-9a-f]{4}$")
_INSTANCE_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def sanitize_instance_name(instance_name: str) -> str:
    if not instance_name:
        return "bot"
    normalized = instance_name.strip().replace(" ", "-")
    normalized = _INSTANCE_INVALID_CHARS_RE.sub("-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = normalized.strip("-.")
    normalized = re.sub(r"^[^a-zA-Z0-9]+", "", normalized)
    if not normalized:
        return "bot"
    return normalized


def name_has_timestamp(instance_name: str) -> bool:
    return bool(_INSTANCE_TIMESTAMP_RE.search(instance_name))


def name_has_suffix(instance_name: str) -> bool:
    return bool(_INSTANCE_SUFFIX_RE.search(instance_name))


def should_generate_unique_name(instance_name: str, unique: bool) -> bool:
    if not unique:
        return False
    normalized = sanitize_instance_name(instance_name)
    return not (name_has_timestamp(normalized) and name_has_suffix(normalized))


def build_controller_instance_name(instance_name: str, unique: bool = True) -> Tuple[str, str, bool]:
    normalized_name = sanitize_instance_name(instance_name)
    if not unique:
        return normalized_name, f"{normalized_name}.yml", False

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    has_timestamp = name_has_timestamp(normalized_name)
    has_suffix = name_has_suffix(normalized_name)

    if has_timestamp:
        unique_instance_name = normalized_name if has_suffix else f"{normalized_name}-{suffix}"
        generated = not has_suffix
    else:
        unique_instance_name = f"{normalized_name}-{timestamp}-{suffix}"
        generated = True

    return unique_instance_name, f"{unique_instance_name}.yml", generated
