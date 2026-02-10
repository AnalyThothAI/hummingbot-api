import unittest


class _CapturingHttpClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"ok": True}

    def post(self, path, json_body=None):
        self.calls.append(("POST", path, json_body))
        return {"ok": True}


class McpToolRegistryClmmPositionInfoTests(unittest.TestCase):
    def test_gateway_clmm_position_info_handler_posts_to_gateway_endpoint(self):
        from mcp.tool_registry import _gateway_clmm_position_info

        http = _CapturingHttpClient()
        result = _gateway_clmm_position_info(
            {
                "connector": "uniswap",
                "network": "ethereum-base",
                "position_address": "4601565",
            },
            http,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            http.calls,
            [
                (
                    "POST",
                    "/gateway/clmm/position-info",
                    {
                        "connector": "uniswap",
                        "network": "ethereum-base",
                        "position_address": "4601565",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()

