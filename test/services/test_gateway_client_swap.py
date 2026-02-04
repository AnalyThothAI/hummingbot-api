import importlib.util
import sys
import types
import unittest
from pathlib import Path


def _load_gateway_client_class():
    sys.modules.setdefault(
        "aiohttp",
        types.SimpleNamespace(ClientSession=object, ClientError=Exception, ClientResponse=object),
    )
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "services" / "gateway_client.py"
    spec = importlib.util.spec_from_file_location("gateway_client", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GatewayClient


GatewayClient = _load_gateway_client_class()


class _CapturingGatewayClient(GatewayClient):
    def __init__(self):
        super().__init__(base_url="http://localhost:15888")
        self.calls = []

    async def _request(self, method, path, params=None, json=None):
        self.calls.append({
            "method": method,
            "path": path,
            "params": params,
            "json": json,
        })
        return {"ok": True}


class GatewayClientSwapTests(unittest.IsolatedAsyncioTestCase):
    async def test_quote_swap_calls_trading_swap_quote(self):
        client = _CapturingGatewayClient()

        await client.quote_swap(
            chain_network="ethereum-bsc",
            base_token="0xbase",
            quote_token="0xquote",
            amount=1.23,
            side="SELL",
            slippage_pct=0.5,
            connector="pancakeswap/router",
        )

        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["path"], "trading/swap/quote")
        self.assertEqual(
            call["params"],
            {
                "chainNetwork": "ethereum-bsc",
                "baseToken": "0xbase",
                "quoteToken": "0xquote",
                "amount": 1.23,
                "side": "SELL",
                "slippagePct": 0.5,
                "connector": "pancakeswap/router",
            },
        )

    async def test_execute_swap_calls_trading_swap_execute(self):
        client = _CapturingGatewayClient()

        await client.execute_swap(
            chain_network="ethereum-bsc",
            wallet_address="0xwallet",
            base_token="0xbase",
            quote_token="0xquote",
            amount=2.5,
            side="BUY",
            slippage_pct=1.0,
            connector="pancakeswap/router",
        )

        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "trading/swap/execute")
        self.assertEqual(
            call["json"],
            {
                "chainNetwork": "ethereum-bsc",
                "walletAddress": "0xwallet",
                "baseToken": "0xbase",
                "quoteToken": "0xquote",
                "amount": 2.5,
                "side": "BUY",
                "slippagePct": 1.0,
                "connector": "pancakeswap/router",
            },
        )


if __name__ == "__main__":
    unittest.main()
