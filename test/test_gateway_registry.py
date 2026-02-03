import os
import sys
import unittest


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)


class TestGatewayRegistryHelpers(unittest.TestCase):
    def test_connector_helpers(self):
        from frontend.components.gateway_registry.common import connector_base_name, connector_pool_type

        self.assertEqual(connector_base_name("uniswap/clmm"), "uniswap")
        self.assertEqual(connector_pool_type("uniswap/clmm"), "clmm")
        self.assertIsNone(connector_pool_type("uniswap"))

    def test_network_helpers(self):
        from frontend.components.gateway_registry.common import extract_network_value, split_network_id

        self.assertEqual(split_network_id("ethereum-mainnet"), ("ethereum", "mainnet"))
        self.assertEqual(extract_network_value("ethereum-mainnet"), "mainnet")
        self.assertEqual(extract_network_value("solana"), "solana")

    def test_address_validators(self):
        from frontend.components.gateway_registry.validators import is_valid_solana_address, normalize_evm_address

        sol = "7EW5dDD6MYJK4PcZ89MGApJQwWeDEeNgH4NCVU4qpump"
        self.assertTrue(is_valid_solana_address(sol))

        evm = "0xb695559b26bb2c9703ef1935c37aeae9526bab07"
        checksum, notice = normalize_evm_address(evm)
        if checksum is None:
            self.skipTest("eth_utils not available for checksum validation")
        self.assertTrue(checksum.startswith("0x"))
        self.assertIsNotNone(notice)

    def test_normalize_pools(self):
        from frontend.components.gateway_registry.normalizers import normalize_existing_pool, normalize_search_pool

        search_pool = {
            "trading_pair": "ETH-USDC",
            "base_symbol": "ETH",
            "quote_symbol": "USDC",
            "base_address": "0xbase",
            "quote_address": "0xquote",
            "fee_tier": 0.01,
            "address": "0xpool",
        }
        normalized = normalize_search_pool(search_pool)
        self.assertEqual(normalized.get("trading_pair"), "ETH-USDC")
        self.assertEqual(normalized.get("base_address"), "0xbase")

        existing_pool = {
            "base": "SOL",
            "quote": "USDC",
            "base_token_address": "base1",
            "quote_token_address": "quote1",
            "address": "pool1",
            "fee_pct": 0.05,
        }
        normalized_existing = normalize_existing_pool(existing_pool)
        self.assertEqual(normalized_existing.get("trading_pair"), "SOL-USDC")
        self.assertEqual(normalized_existing.get("base_address"), "base1")

    def test_pool_payload_builder(self):
        from frontend.components.gateway_registry.ensure import build_add_pool_payload, pool_exists

        pool = {
            "trading_pair": "ETH-USDC",
            "base_symbol": "ETH",
            "quote_symbol": "USDC",
            "base_address": "0xbase",
            "quote_address": "0xquote",
            "fee_tier": 0.01,
            "address": "0xpool",
        }
        payload = build_add_pool_payload(
            connector_name="uniswap",
            network_id="ethereum-mainnet",
            pool_type="clmm",
            pool=pool,
        )
        self.assertEqual(payload.get("connector_name"), "uniswap")
        self.assertEqual(payload.get("network"), "mainnet")
        self.assertEqual(payload.get("address"), "0xpool")
        self.assertEqual(payload.get("base"), "ETH")
        self.assertTrue("fee_pct" in payload)

        existing = [{"address": "0xpool"}]
        self.assertTrue(pool_exists(existing, "0xpool"))
        self.assertFalse(pool_exists(existing, "0xother"))


if __name__ == "__main__":
    unittest.main()
