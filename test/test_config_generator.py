import os
import sys
import unittest


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

from frontend.components import controller_config_generator as generator  # noqa: E402
from frontend.components.controller_config_generator_helpers import (  # noqa: E402
    select_default_controller_type_index,
)


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

    def test_pool_to_override_row_uses_base_quote_when_missing_pair(self):
        pool = {"base": "SOL", "quote": "USDC", "address": "0xpool"}
        row = generator.pool_to_override_row(pool)
        self.assertEqual(row["trading_pair"], "SOL-USDC")
        self.assertEqual(row["pool_trading_pair"], "SOL-USDC")
        self.assertEqual(row["pool_address"], "0xpool")

    def test_merge_override_rows_prefers_new_rows_when_requested(self):
        existing = [{"trading_pair": "SOL-USDC", "pool_address": "0xold"}]
        new = [{"trading_pair": "SOL-USDC", "pool_address": "0xnew"}]
        merged = generator.merge_override_rows(existing, new, prefer_new=True)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["pool_address"], "0xnew")

    def test_compute_param_overrides_filters_unchanged_and_none(self):
        base = {"position_value_quote": 10, "rebalance_enabled": False}
        values = {"position_value_quote": 10, "rebalance_enabled": True, "stop_loss_pnl_pct": None}
        overrides = generator.compute_param_overrides(base, values)
        self.assertEqual(overrides, {"rebalance_enabled": True})

    def test_compute_param_overrides_treats_numeric_equal(self):
        base = {"exit_swap_slippage_pct": "0.005"}
        values = {"exit_swap_slippage_pct": 0.005}
        overrides = generator.compute_param_overrides(base, values)
        self.assertEqual(overrides, {})

    def test_apply_param_overrides_updates_only_provided_fields(self):
        base = {
            "position_value_quote": 10,
            "position_width_pct": 5,
            "rebalance_enabled": False,
        }
        overrides = {
            "position_value_quote": 20,
            "rebalance_enabled": True,
            "stop_loss_pnl_pct": None,
        }
        updated = generator.apply_param_overrides(base, overrides)
        self.assertEqual(updated["position_value_quote"], 20)
        self.assertEqual(updated["position_width_pct"], 5)
        self.assertTrue(updated["rebalance_enabled"])
        self.assertNotIn("stop_loss_pnl_pct", updated)

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

    def test_select_default_controller_type_index_prefers_generic(self):
        types = ["directional_trading", "generic", "market_making"]
        self.assertEqual(select_default_controller_type_index(types), 1)

    def test_select_default_controller_type_index_falls_back_to_zero(self):
        types = ["directional_trading", "market_making"]
        self.assertEqual(select_default_controller_type_index(types), 0)


if __name__ == "__main__":
    unittest.main()
