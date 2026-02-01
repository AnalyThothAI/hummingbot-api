import json
import os
import urllib.parse
import urllib.request

import pytest


GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:15888")
GATEWAY_CONNECTOR = os.getenv("GATEWAY_CONNECTOR", "uniswap")
GATEWAY_NETWORK = os.getenv("GATEWAY_NETWORK", "bsc")
_pair_raw = os.getenv("GATEWAY_PAIR", "\\u6211\\u8e0f\\u9a6c\\u6765\\u4e86-USDT")
if "\\u" in _pair_raw:
    GATEWAY_PAIR = _pair_raw.encode("utf-8").decode("unicode_escape")
else:
    GATEWAY_PAIR = _pair_raw

if os.getenv("GATEWAY_INTEGRATION") != "1":
    pytest.skip("gateway integration tests disabled (set GATEWAY_INTEGRATION=1)", allow_module_level=True)


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)


def _quote_swap(pool_address: str, base: str, quote: str):
    params = {
        "network": GATEWAY_NETWORK,
        "poolAddress": pool_address,
        "baseToken": base,
        "quoteToken": quote,
        "amount": "1",
        "side": "SELL",
    }
    qs = urllib.parse.urlencode(params)
    url = f"{GATEWAY_URL}/connectors/{GATEWAY_CONNECTOR}/clmm/quote-swap?{qs}"
    return _get_json(url)


def test_uniswap_pool_info_token0_order_and_price_direction():
    encoded_pair = urllib.parse.quote(GATEWAY_PAIR, safe="")
    pool_url = (
        f"{GATEWAY_URL}/pools/{encoded_pair}"
        f"?connector={GATEWAY_CONNECTOR}&network={GATEWAY_NETWORK}&type=clmm"
    )
    pool = _get_json(pool_url)
    pool_address = pool.get("address")
    assert pool_address

    info_url = (
        f"{GATEWAY_URL}/connectors/{GATEWAY_CONNECTOR}/clmm/pool-info"
        f"?network={GATEWAY_NETWORK}&poolAddress={pool_address}"
    )
    info = _get_json(info_url)
    token0 = info.get("baseTokenAddress")
    token1 = info.get("quoteTokenAddress")
    assert token0 and token1

    assert token0.lower() < token1.lower()

    price_token0 = _quote_swap(pool_address, token0, token1)["price"]
    price_token1 = _quote_swap(pool_address, token1, token0)["price"]
    assert price_token0 > 0
    assert price_token1 > 0
    assert abs(price_token0 * price_token1 - 1) < 0.35
