import os
import sys
import types
import asyncio
from decimal import Decimal

dummy_async_utils = types.ModuleType("hummingbot.core.utils.async_utils")


def _safe_ensure_future(coro):
    return asyncio.create_task(coro)


dummy_async_utils.safe_ensure_future = _safe_ensure_future
sys.modules.setdefault("hummingbot.core.utils.async_utils", dummy_async_utils)

dummy_logger_module = types.ModuleType("hummingbot.logger")


class _HBLogger:
    def warning(self, *args, **kwargs):
        pass


dummy_logger_module.HummingbotLogger = _HBLogger
sys.modules.setdefault("hummingbot.logger", dummy_logger_module)
dummy_struct_logger = types.ModuleType("hummingbot.logger.struct_logger")
dummy_struct_logger.StructLogger = _HBLogger
dummy_struct_logger.StructLogRecord = object
sys.modules.setdefault("hummingbot.logger.struct_logger", dummy_struct_logger)

dummy_connector_utils = types.ModuleType("hummingbot.connector.utils")


def _split_hb_trading_pair(trading_pair: str):
    parts = trading_pair.split("-")
    return parts[0], parts[1] if len(parts) > 1 else ""


dummy_connector_utils.split_hb_trading_pair = _split_hb_trading_pair
sys.modules.setdefault("hummingbot.connector.utils", dummy_connector_utils)


class _GatewayStub:
    def __init__(self, *, chain="ethereum", network="bsc", error=None, price=None):
        self._chain = chain
        self._network = network
        self._error = error
        self._price = price

    async def get_connector_chain_network(self, _connector):
        return self._chain, self._network, self._error

    async def get_price(self, *args, **kwargs):
        if self._price is None:
            return {}
        return {"price": self._price}


dummy_gateway_module = types.ModuleType("hummingbot.core.gateway.gateway_http_client")


class _GatewayHttpClient:
    _instance = None

    @classmethod
    def get_instance(cls):
        return cls._instance


dummy_gateway_module.GatewayHttpClient = _GatewayHttpClient
sys.modules["hummingbot.core.gateway.gateway_http_client"] = dummy_gateway_module

# ---- Import target after stubs ----
sys.modules.pop("bots.controllers.generic.clmm_lp_domain.io", None)
from bots.controllers.generic.clmm_lp_domain.io import PriceProvider


class _MDP:
    def __init__(self, now):
        self._now = now

    def time(self):
        return self._now


class _Logger:
    def warning(self, *args, **kwargs):
        pass


def test_price_provider_returns_unavailable_on_gateway_error():
    _GatewayHttpClient._instance = _GatewayStub(error="network_error")
    provider = PriceProvider(
        connector_name="uniswap/router",
        trading_pair="AAA-BBB",
        market_data_provider=_MDP(now=100),
        logger=lambda: _Logger(),
    )

    async def _run():
        await provider._refresh_price()
        return provider.get_price_context(now=100)

    ctx = asyncio.run(_run())

    assert ctx.value is None
    assert ctx.source == "unavailable"


def test_price_provider_sets_price_on_success():
    _GatewayHttpClient._instance = _GatewayStub(price="2.5")
    provider = PriceProvider(
        connector_name="uniswap/router",
        trading_pair="AAA-BBB",
        market_data_provider=_MDP(now=200),
        logger=lambda: _Logger(),
    )

    async def _run():
        await provider._refresh_price()
        return provider.get_price_context(now=200)

    ctx = asyncio.run(_run())

    assert ctx.value == Decimal("2.5")
    assert ctx.source == "gateway_direct:uniswap/router"
