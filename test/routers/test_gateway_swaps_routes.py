import unittest
from pathlib import Path


class GatewaySwapsRoutesTests(unittest.TestCase):
    def test_gateway_swaps_routes_exist(self):
        repo_root = Path(__file__).resolve().parents[2]
        router_path = repo_root / "routers" / "gateway_swaps.py"
        self.assertTrue(router_path.exists(), "routers/gateway_swaps.py should exist")
        content = router_path.read_text(encoding="utf-8")
        for needle in (
            '"/swaps/{transaction_hash}/status"',
            '"/swaps/search"',
            '"/swaps/summary"',
        ):
            self.assertIn(needle, content)


if __name__ == "__main__":
    unittest.main()
