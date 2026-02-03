import json
from typing import Any, Dict, Type

try:
    from pydantic_core import PydanticUndefined
except Exception:  # pragma: no cover - fallback for older versions
    PydanticUndefined = object()


def build_controller_config_schema(config_class: Type) -> Dict[str, Any]:
    schema = config_class.model_json_schema() if hasattr(config_class, "model_json_schema") else {}

    defaults: Dict[str, Any] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    for name, field in getattr(config_class, "model_fields", {}).items():
        default = getattr(field, "default", None)
        if default is PydanticUndefined:
            default = None
        defaults[name] = default
        json_schema_extra = getattr(field, "json_schema_extra", None) or {}
        meta[name] = dict(json_schema_extra) if isinstance(json_schema_extra, dict) else {}

    payload = {
        "schema": schema,
        "defaults": defaults,
        "meta": meta,
    }
    # Ensure JSON serializable (e.g., Decimal)
    return json.loads(json.dumps(payload, default=str))
