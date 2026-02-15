import os
import sys
import types
import asyncio
from types import SimpleNamespace

# ---- Stubs for hummingbot dependencies ----
dummy_async_utils = types.ModuleType("hummingbot.core.utils.async_utils")


def _safe_ensure_future(coro):
    return asyncio.create_task(coro)


dummy_async_utils.safe_ensure_future = _safe_ensure_future
sys.modules.setdefault("hummingbot.core.utils.async_utils", dummy_async_utils)

dummy_connector_utils = types.ModuleType("hummingbot.connector.utils")


def _split_hb_trading_pair(trading_pair: str):
    parts = trading_pair.split("-")
    return parts[0], parts[1] if len(parts) > 1 else ""


dummy_connector_utils.split_hb_trading_pair = _split_hb_trading_pair
sys.modules.setdefault("hummingbot.connector.utils", dummy_connector_utils)

dummy_executor_types = types.ModuleType("hummingbot.strategy_v2.executors.data_types")


class _ConnectorPair:
    def __init__(self, *args, **kwargs):
        pass


dummy_executor_types.ConnectorPair = _ConnectorPair
sys.modules.setdefault("hummingbot.strategy_v2.executors.data_types", dummy_executor_types)

dummy_clmm_lp_base = types.ModuleType("bots.controllers.generic.clmm_lp_base")


class _BaseConfig:
    pass


class _BaseController:
    pass


dummy_clmm_lp_base.CLMMLPBaseConfig = _BaseConfig
dummy_clmm_lp_base.CLMMLPBaseController = _BaseController
sys.modules.setdefault("bots.controllers.generic.clmm_lp_base", dummy_clmm_lp_base)

dummy_components = types.ModuleType("bots.controllers.generic.clmm_lp_domain.components")


class _PoolDomainAdapter:
    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def from_config(cls, trading_pair, pool_trading_pair):
        return cls()


dummy_components.PoolDomainAdapter = _PoolDomainAdapter
sys.modules.setdefault("bots.controllers.generic.clmm_lp_domain.components", dummy_components)

dummy_clmm_fsm = types.ModuleType("bots.controllers.generic.clmm_lp_domain.clmm_fsm")
dummy_clmm_fsm.CLMMFSM = object
sys.modules.setdefault("bots.controllers.generic.clmm_lp_domain.clmm_fsm", dummy_clmm_fsm)

dummy_io = types.ModuleType("bots.controllers.generic.clmm_lp_domain.io")
dummy_io.ActionFactory = object
dummy_io.BalanceManager = object
dummy_io.SnapshotBuilder = object
sys.modules.setdefault("bots.controllers.generic.clmm_lp_domain.io", dummy_io)

dummy_policies = types.ModuleType("bots.controllers.generic.clmm_lp_domain.policies")
dummy_policies.UniswapV3Policy = object
sys.modules.setdefault("bots.controllers.generic.clmm_lp_domain.policies", dummy_policies)


class _GatewayStub:
    def __init__(self, *, pool_info, tokens, allow_fallback: bool = False):
        self._pool_info = pool_info
        self._tokens = tokens
        self._allow_fallback = allow_fallback
        self.api_request_called = False

    async def get_connector_chain_network(self, _connector):
        return "ethereum", "bsc", None

    async def pool_info(self, *args, **kwargs):
        return self._pool_info

    async def get_token(self, address, *_args, **_kwargs):
        return self._tokens.get(address, {})

    async def api_request(self, *args, **kwargs):
        self.api_request_called = True
        if not self._allow_fallback:
            raise AssertionError("fallback api_request should not be called")
        return []


dummy_gateway_module = types.ModuleType("hummingbot.core.gateway.gateway_http_client")


class _GatewayHttpClient:
    _instance = None

    @classmethod
    def get_instance(cls):
        return cls._instance


dummy_gateway_module.GatewayHttpClient = _GatewayHttpClient
sys.modules["hummingbot.core.gateway.gateway_http_client"] = dummy_gateway_module

# ---- Import target module after stubs ----
sys.modules.pop("bots.controllers.generic.clmm_lp_uniswap", None)
from bots.controllers.generic.clmm_lp_uniswap import _UniswapPoolDomainResolver


def test_pool_info_uses_token0_token1_order_from_address_sort():
    pool_info = {
        "baseTokenAddress": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "quoteTokenAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    }
    tokens = {
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {"token": {"symbol": "AAA"}},
        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": {"token": {"symbol": "BBB"}},
    }
    gateway = _GatewayStub(pool_info=pool_info, tokens=tokens)
    _GatewayHttpClient._instance = gateway

    cfg = SimpleNamespace(
        connector_name="uniswap/clmm",
        trading_pair="AAA-BBB",
        pool_address="0xpool",
    )
    resolver = _UniswapPoolDomainResolver(
        config=cfg,
        ctx=SimpleNamespace(),
        apply_domain=lambda _domain: None,
        market_data_provider=SimpleNamespace(time=lambda: 0),
    )

    pool_pair, error = asyncio.run(resolver._resolve_pool_trading_pair(cfg.pool_address))

    assert error is None
    assert pool_pair == "AAA-BBB"


def test_pool_info_missing_addresses_does_not_fallback():
    pool_info = {}
    gateway = _GatewayStub(pool_info=pool_info, tokens={})
    _GatewayHttpClient._instance = gateway

    cfg = SimpleNamespace(
        connector_name="uniswap/clmm",
        trading_pair="AAA-BBB",
        pool_address="0xpool",
    )
    resolver = _UniswapPoolDomainResolver(
        config=cfg,
        ctx=SimpleNamespace(),
        apply_domain=lambda _domain: None,
        market_data_provider=SimpleNamespace(time=lambda: 0),
    )

    pool_pair, error = asyncio.run(resolver._resolve_pool_trading_pair(cfg.pool_address))

    assert pool_pair is None
    assert error == "pool_data_invalid"
    assert gateway.api_request_called is False


def test_pool_info_accepts_weth_eth_symbol_alias_match():
    pool_info = {
        "baseTokenAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "quoteTokenAddress": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }
    tokens = {
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {"token": {"symbol": "WETH"}},
        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": {"token": {"symbol": "USDC"}},
    }
    gateway = _GatewayStub(pool_info=pool_info, tokens=tokens)
    _GatewayHttpClient._instance = gateway

    cfg = SimpleNamespace(
        connector_name="uniswap/clmm",
        trading_pair="ETH-USDC",
        pool_address="0xpool",
    )
    resolver = _UniswapPoolDomainResolver(
        config=cfg,
        ctx=SimpleNamespace(),
        apply_domain=lambda _domain: None,
        market_data_provider=SimpleNamespace(time=lambda: 0),
    )

    pool_pair, error = asyncio.run(resolver._resolve_pool_trading_pair(cfg.pool_address))

    assert error is None
    assert pool_pair == "WETH-USDC"
