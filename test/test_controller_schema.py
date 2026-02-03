import unittest
from decimal import Decimal

from utils.controller_schema import build_controller_config_schema


class _DummyField:
    def __init__(self, default, annotation=str, json_schema_extra=None):
        self.default = default
        self.annotation = annotation
        self.json_schema_extra = json_schema_extra or {}


class DummyConfig:
    model_fields = {
        "controller_name": _DummyField("dummy_controller", annotation=str),
        "position_value_quote": _DummyField(Decimal("0.3"), annotation=Decimal),
        "enabled": _DummyField(True, annotation=bool),
        "mode": _DummyField("a", annotation=str, json_schema_extra={"locked": True}),
    }

    @staticmethod
    def model_json_schema():
        return {
            "type": "object",
            "properties": {
                "controller_name": {"type": "string", "default": "dummy_controller"},
                "position_value_quote": {"type": "number", "default": "0.3"},
                "enabled": {"type": "boolean", "default": True},
                "mode": {"type": "string", "default": "a", "enum": ["a", "b"]},
            },
            "required": ["controller_name"],
        }


class BrokenSchemaConfig:
    model_fields = {
        "count": _DummyField(1, annotation=int),
        "enabled": _DummyField(False, annotation=bool),
        "name": _DummyField("alpha", annotation=str),
    }

    @staticmethod
    def model_json_schema():
        raise RuntimeError("boom")


class ControllerSchemaTests(unittest.TestCase):
    def test_build_controller_config_schema_includes_defaults_and_meta(self):
        payload = build_controller_config_schema(DummyConfig)

        self.assertIn("schema", payload)
        self.assertIn("defaults", payload)
        self.assertIn("meta", payload)

        defaults = payload["defaults"]
        self.assertEqual(defaults["controller_name"], "dummy_controller")
        self.assertEqual(defaults["position_value_quote"], "0.3")
        self.assertTrue(defaults["enabled"])

        meta = payload["meta"]
        self.assertEqual(meta["mode"].get("locked"), True)

    def test_build_controller_config_schema_falls_back_when_schema_errors(self):
        payload = build_controller_config_schema(BrokenSchemaConfig)
        schema = payload["schema"]
        self.assertIn("properties", schema)
        props = schema["properties"]
        self.assertEqual(props["count"]["type"], "integer")
        self.assertEqual(props["enabled"]["type"], "boolean")
        self.assertEqual(props["name"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
