import asyncio
import logging
from decimal import Decimal

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
    def __init__(self, connectors, now: float):
        self.connectors = dict(connectors)
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


def test_balance_snapshot_ignored_when_missing_token_and_previous_positive():
    connector = FakeConnector([{"QUOTE": Decimal("1")}])
    mdp = FakeMarketDataProvider({"router": connector}, now=200.0)
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

    asyncio.run(manager._update_wallet_balances())

    assert manager.wallet_base == Decimal("10")
    assert manager.wallet_quote == Decimal("5")
    assert manager.last_update_ts == 100.0
    assert manager.wallet_source == "router"


def test_balance_snapshot_ignored_when_missing_token_even_if_previous_zero():
    connector = FakeConnector([{"QUOTE": Decimal("2")}])
    mdp = FakeMarketDataProvider({"router": connector}, now=200.0)
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

    asyncio.run(manager._update_wallet_balances())

    assert manager.wallet_base == Decimal("0")
    assert manager.wallet_quote == Decimal("0")
    assert manager.last_update_ts == 100.0
    assert manager.wallet_source == "router"


def test_balance_snapshot_falls_back_to_lp_connector_when_router_missing_tokens():
    router = FakeConnector([{"QUOTE": Decimal("2")}])  # missing BASE
    lp = FakeConnector([{"BASE": Decimal("3"), "QUOTE": Decimal("4")}])
    mdp = FakeMarketDataProvider({"router": router, "lp": lp}, now=200.0)
    config = DummyConfig()
    domain = DummyDomain("BASE", "QUOTE")
    manager = BalanceManager(
        config=config,
        domain=domain,
        market_data_provider=mdp,
        logger=lambda: logging.getLogger("test"),
    )

    asyncio.run(manager._update_wallet_balances())

    assert manager.wallet_base == Decimal("3")
    assert manager.wallet_quote == Decimal("4")
    assert manager.wallet_source == "lp"

