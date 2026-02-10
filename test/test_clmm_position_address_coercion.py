import importlib.util
from pathlib import Path


def _load_gateway_trading_module():
    # Import `models/gateway_trading.py` without importing `models/__init__.py` (which depends on hummingbot core).
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "models" / "gateway_trading.py"
    spec = importlib.util.spec_from_file_location("gateway_trading_under_test", module_path)
    assert spec and spec.loader, "Failed to create module spec for models/gateway_trading.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clmm_requests_accept_int_position_address_and_coerce_to_str():
    gt = _load_gateway_trading_module()

    # Some CLMM connectors on EVM use numeric token IDs (e.g., Uniswap V3 tokenId).
    # External callers may send JSON numbers. We accept ints and normalize to str.
    req = gt.CLMMClosePositionRequest(
        connector="uniswap",
        network="ethereum-base",
        position_address=4601565,
    )
    assert req.position_address == "4601565"

    req = gt.CLMMCollectFeesRequest(
        connector="uniswap",
        network="ethereum-base",
        position_address=4601565,
    )
    assert req.position_address == "4601565"

    req = gt.CLMMGetPositionInfoRequest(
        connector="uniswap",
        network="ethereum-base",
        position_address=4601565,
    )
    assert req.position_address == "4601565"

    req = gt.CLMMAddLiquidityRequest(
        connector="uniswap",
        network="ethereum-base",
        position_address=4601565,
        base_token_amount=None,
        quote_token_amount=None,
        slippage_pct=1,
    )
    assert req.position_address == "4601565"

    req = gt.CLMMRemoveLiquidityRequest(
        connector="uniswap",
        network="ethereum-base",
        position_address=4601565,
        percentage=100,
    )
    assert req.position_address == "4601565"
