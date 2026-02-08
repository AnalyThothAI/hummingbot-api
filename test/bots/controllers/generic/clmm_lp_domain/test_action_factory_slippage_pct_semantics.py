import asyncio
import os
import sys
import types
from decimal import Decimal

import pytest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


dummy_gateway_module = types.ModuleType("hummingbot.core.gateway.gateway_http_client")


class DummyGatewayHttpClient:
    @staticmethod
    def get_instance(*_args, **_kwargs):
        return None


dummy_gateway_module.GatewayHttpClient = DummyGatewayHttpClient
sys.modules.setdefault("hummingbot.core.gateway.gateway_http_client", dummy_gateway_module)

dummy_async_utils = types.ModuleType("hummingbot.core.utils.async_utils")


def _safe_ensure_future(coro):
    return asyncio.create_task(coro)


dummy_async_utils.safe_ensure_future = _safe_ensure_future
sys.modules.setdefault("hummingbot.core.utils.async_utils", dummy_async_utils)


sys.modules.pop("bots.controllers.generic.clmm_lp_domain.io", None)
from bots.controllers.generic.clmm_lp_domain.io import ActionFactory


class DummyConfig:
    def __init__(self, slippage_pct: Decimal):
        self.id = "test"
        self.connector_name = "uniswap/clmm"
        self.router_connector = "uniswap/router"
        self.trading_pair = "ETH-USDC"
        self.pool_address = ""
        self.exit_swap_slippage_pct = slippage_pct


def _make_action_factory(cfg: DummyConfig) -> ActionFactory:
    return ActionFactory(
        config=cfg,
        domain=types.SimpleNamespace(),
        budget_key="test",
        budget_coordinator=None,
        market_data_provider=types.SimpleNamespace(connectors={}),
        extra_lp_params=lambda: None,
    )


def test_swap_slippage_pct_accepts_ratio():
    af = _make_action_factory(DummyConfig(Decimal("0.01")))  # 1%
    assert af.swap_slippage_pct() == Decimal("1")


def test_swap_slippage_pct_rejects_large_ratio_guardrail():
    af = _make_action_factory(DummyConfig(Decimal("0.25")))  # 25%
    with pytest.raises(ValueError):
        af.swap_slippage_pct()
