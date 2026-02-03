import json
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Type, get_args, get_origin

try:
    from pydantic_core import PydanticUndefined
except Exception:  # pragma: no cover - fallback for older versions
    PydanticUndefined = object()


def _map_annotation_to_schema(annotation: Any) -> Dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is list or origin is tuple:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _map_annotation_to_schema(item_type)}
    if origin is dict:
        return {"type": "object"}
    if origin is None and args:
        # Optional/Union
        non_null = [arg for arg in args if arg is not type(None)]  # noqa: E721
        if non_null:
            return _map_annotation_to_schema(non_null[0])

    if annotation in (int,):
        return {"type": "integer"}
    if annotation in (float, Decimal):
        return {"type": "number"}
    if annotation in (bool,):
        return {"type": "boolean"}
    if annotation in (str,):
        return {"type": "string"}
    if annotation in (dict,):
        return {"type": "object"}
    if annotation in (list, tuple):
        return {"type": "array", "items": {}}

    try:
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return {"type": "string", "enum": [item.value for item in annotation]}
    except TypeError:
        pass

    return {"type": "string"}


def _build_schema_from_fields(config_class: Type) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required = []
    for name, field in getattr(config_class, "model_fields", {}).items():
        annotation = getattr(field, "annotation", None)
        properties[name] = _map_annotation_to_schema(annotation)
        default = getattr(field, "default", None)
        if default is PydanticUndefined:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def build_controller_config_schema(config_class: Type) -> Dict[str, Any]:
    schema = {}
    if hasattr(config_class, "model_json_schema"):
        try:
            schema = config_class.model_json_schema()
        except Exception:
            schema = _build_schema_from_fields(config_class)
    else:
        schema = _build_schema_from_fields(config_class)

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
