import unittest
from decimal import Decimal

from utils.controller_schema import build_controller_config_schema


class _DummyField:
    def __init__(self, default, json_schema_extra=None):
        self.default = default
        self.json_schema_extra = json_schema_extra or {}


class DummyConfig:
    model_fields = {
        "controller_name": _DummyField("dummy_controller"),
        "position_value_quote": _DummyField(Decimal("0.3")),
        "enabled": _DummyField(True),
        "mode": _DummyField("a", json_schema_extra={"locked": True}),
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


if __name__ == "__main__":
    unittest.main()
