import os
import sys
import unittest


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

from frontend.components import controller_config_generator as generator  # noqa: E402


class ConfigGeneratorTests(unittest.TestCase):
    def test_parse_override_rows_supports_csv_and_trading_pair_only(self):
        raw = "ETH-USDT\nSOL-USDT, SOL-USDT, 0xabc\n\n"
        rows = generator.parse_override_rows(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["trading_pair"], "ETH-USDT")
        self.assertIsNone(rows[0]["pool_trading_pair"])
        self.assertIsNone(rows[0]["pool_address"])
        self.assertEqual(rows[1]["trading_pair"], "SOL-USDT")
        self.assertEqual(rows[1]["pool_trading_pair"], "SOL-USDT")
        self.assertEqual(rows[1]["pool_address"], "0xabc")

    def test_validate_override_requires_pool_address_when_pair_changes(self):
        base_config = {"trading_pair": "ETH-USDT", "pool_address": "0xold"}
        row = {"trading_pair": "SOL-USDT", "pool_trading_pair": None, "pool_address": None}
        errors = generator.validate_override_row(base_config, row)
        self.assertTrue(any("pool_address" in err for err in errors))

    def test_build_override_payload_defaults_pool_trading_pair_for_clmm(self):
        base_config = {"connector_name": "raydium/clmm", "trading_pair": "ETH-USDT"}
        row = {"trading_pair": "SOL-USDT", "pool_trading_pair": None, "pool_address": None}
        payload = generator.build_override_payload(base_config, row)
        self.assertEqual(payload["trading_pair"], "SOL-USDT")
        self.assertEqual(payload["pool_trading_pair"], "SOL-USDT")

    def test_build_override_payload_overrides_pool_address_when_provided(self):
        base_config = {"trading_pair": "ETH-USDT", "pool_address": "0xold"}
        row = {"trading_pair": "ETH-USDT", "pool_trading_pair": None, "pool_address": "0xnew"}
        payload = generator.build_override_payload(base_config, row)
        self.assertEqual(payload["pool_address"], "0xnew")


if __name__ == "__main__":
    unittest.main()
