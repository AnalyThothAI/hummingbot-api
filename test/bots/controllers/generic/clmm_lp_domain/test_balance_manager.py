import logging
import os
import sys
import asyncio
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

dummy_connector_utils = types.ModuleType("hummingbot.connector.utils")


def _split_hb_trading_pair(trading_pair: str):
    parts = trading_pair.split("-")
    return parts[0], parts[1] if len(parts) > 1 else ""


dummy_connector_utils.split_hb_trading_pair = _split_hb_trading_pair
sys.modules.setdefault("hummingbot.connector.utils", dummy_connector_utils)

dummy_components = types.ModuleType("bots.controllers.generic.clmm_lp_domain.components")


class _Dummy:
    def __init__(self, *args, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


dummy_components.LPView = _Dummy
dummy_components.PoolDomainAdapter = _Dummy
dummy_components.PriceContext = _Dummy
dummy_components.Snapshot = _Dummy
dummy_components.SwapPurpose = _Dummy
dummy_components.SwapView = _Dummy
sys.modules.setdefault("bots.controllers.generic.clmm_lp_domain.components", dummy_components)

dummy_gateway_swap_types = types.ModuleType("hummingbot.strategy_v2.executors.gateway_swap_executor.data_types")
dummy_gateway_swap_types.GatewaySwapExecutorConfig = _Dummy
sys.modules.setdefault(
    "hummingbot.strategy_v2.executors.gateway_swap_executor.data_types", dummy_gateway_swap_types
)

dummy_lp_types = types.ModuleType("hummingbot.strategy_v2.executors.lp_position_executor.data_types")
dummy_lp_types.LPPositionExecutorConfig = _Dummy
sys.modules.setdefault("hummingbot.strategy_v2.executors.lp_position_executor.data_types", dummy_lp_types)

dummy_executor_actions = types.ModuleType("hummingbot.strategy_v2.models.executor_actions")
dummy_executor_actions.CreateExecutorAction = _Dummy
sys.modules.setdefault("hummingbot.strategy_v2.models.executor_actions", dummy_executor_actions)

dummy_executors_info = types.ModuleType("hummingbot.strategy_v2.models.executors_info")
dummy_executors_info.ExecutorInfo = _Dummy
sys.modules.setdefault("hummingbot.strategy_v2.models.executors_info", dummy_executors_info)

from bots.controllers.generic.clmm_lp_domain.io import BalanceManager


class FakeConnector:
    def __init__(self, balances_sequence):
        self._balances_sequence = list(balances_sequence)
        self.available_balances = {}

    async def update_balances(self, token_symbols=None):
        if self._balances_sequence:
            self.available_balances = self._balances_sequence.pop(0)

    def get_available_balance(self, currency: str):
        return self.available_balances.get(currency, Decimal("0"))


class FakeMarketDataProvider:
    def __init__(self, connector, now: float):
        self.connectors = {"router": connector}
        self._now = now

    def time(self) -> float:
        return self._now


class DummyConfig:
    def __init__(self):
        self.connector_name = "lp"
        self.router_connector = "router"
        self.balance_update_timeout_sec = 10
        self.balance_refresh_timeout_sec = 30
        self.native_token_symbol = None


class DummyDomain:
    def __init__(self, base_token: str, quote_token: str):
        self.base_token = base_token
        self.quote_token = quote_token


@pytest.mark.asyncio
async def test_balance_snapshot_ignored_when_missing_token_and_previous_positive():
    connector = FakeConnector([{ "QUOTE": Decimal("1") }])
    mdp = FakeMarketDataProvider(connector, now=200.0)
    config = DummyConfig()
    domain = DummyDomain("BASE", "QUOTE")
    manager = BalanceManager(
        config=config,
        domain=domain,
        market_data_provider=mdp,
        logger=lambda: logging.getLogger("test"),
    )
    manager._wallet_base = Decimal("10")
    manager._wallet_quote = Decimal("5")
    manager._wallet_source = "router"
    manager._last_balance_update_ts = 100.0
    manager._has_balance_snapshot = True

    await manager._update_wallet_balances()

    assert manager.wallet_base == Decimal("10")
    assert manager.wallet_quote == Decimal("5")
    assert manager._last_balance_update_ts == 100.0
    assert manager.wallet_source == "router"


@pytest.mark.asyncio
async def test_balance_snapshot_accepts_missing_token_when_previous_zero():
    connector = FakeConnector([{ "QUOTE": Decimal("2") }])
    mdp = FakeMarketDataProvider(connector, now=200.0)
    config = DummyConfig()
    domain = DummyDomain("BASE", "QUOTE")
    manager = BalanceManager(
        config=config,
        domain=domain,
        market_data_provider=mdp,
        logger=lambda: logging.getLogger("test"),
    )
    manager._wallet_base = Decimal("0")
    manager._wallet_quote = Decimal("0")
    manager._wallet_source = "router"
    manager._last_balance_update_ts = 100.0
    manager._has_balance_snapshot = True

    await manager._update_wallet_balances()

    assert manager.wallet_base == Decimal("0")
    assert manager.wallet_quote == Decimal("2")
    assert manager._last_balance_update_ts == 200.0
    assert manager.wallet_source == "router"
