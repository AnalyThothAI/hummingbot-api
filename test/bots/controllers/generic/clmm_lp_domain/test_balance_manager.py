import logging
from decimal import Decimal

import pytest

from bots.controllers.generic.clmm_lp_base import CLMMLPBaseConfig
from bots.controllers.generic.clmm_lp_domain.components import PoolDomainAdapter
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


@pytest.mark.asyncio
async def test_balance_snapshot_ignored_when_missing_token_and_previous_positive():
    connector = FakeConnector([{ "QUOTE": Decimal("1") }])
    mdp = FakeMarketDataProvider(connector, now=200.0)
    config = CLMMLPBaseConfig(
        connector_name="lp",
        router_connector="router",
        trading_pair="BASE-QUOTE",
    )
    domain = PoolDomainAdapter.from_config("BASE-QUOTE", None)
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
    config = CLMMLPBaseConfig(
        connector_name="lp",
        router_connector="router",
        trading_pair="BASE-QUOTE",
    )
    domain = PoolDomainAdapter.from_config("BASE-QUOTE", None)
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
