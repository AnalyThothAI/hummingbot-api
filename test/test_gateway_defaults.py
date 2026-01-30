import unittest

from utils.gateway_defaults import should_apply_gateway_defaults


class GatewayDefaultsTests(unittest.TestCase):
    def test_requires_flag_and_values(self):
        self.assertTrue(should_apply_gateway_defaults(True, "ethereum-mainnet", None))
        self.assertTrue(should_apply_gateway_defaults(True, None, "0xabc"))
        self.assertFalse(should_apply_gateway_defaults(True, None, None))
        self.assertFalse(should_apply_gateway_defaults(False, "ethereum-mainnet", "0xabc"))
