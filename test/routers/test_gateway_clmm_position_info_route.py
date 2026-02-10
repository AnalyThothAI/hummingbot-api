import unittest
from pathlib import Path


class GatewayClmmPositionInfoRouteTests(unittest.TestCase):
    def test_position_info_route_exists(self):
        repo_root = Path(__file__).resolve().parents[2]
        router_path = repo_root / "routers" / "gateway_clmm.py"
        self.assertTrue(router_path.exists(), "routers/gateway_clmm.py should exist")

        content = router_path.read_text(encoding="utf-8")
        self.assertIn('"/clmm/position-info"', content)


if __name__ == "__main__":
    unittest.main()

