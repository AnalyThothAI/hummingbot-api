import os
import sys
from decimal import Decimal


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


from bots.controllers.generic.clmm_lp_domain.components import PoolDomainAdapter


class _DummyExecutorConfig:
    def __init__(self, *, base_token: str, quote_token: str, trading_pair: str):
        self.base_token = base_token
        self.quote_token = quote_token
        self.trading_pair = trading_pair


class _DummyExecutor:
    def __init__(self, config: _DummyExecutorConfig):
        self.config = config


def test_domain_not_inverted_when_pool_pair_matches_trading_pair():
    domain = PoolDomainAdapter.from_config("SOL-USDC", None)
    assert domain.pool_order_inverted is False
    assert domain.base_token == "SOL"
    assert domain.quote_token == "USDC"
    assert domain.pool_base_token == "SOL"
    assert domain.pool_quote_token == "USDC"

    assert domain.strategy_amounts_to_pool(Decimal("1"), Decimal("2")) == (Decimal("1"), Decimal("2"))
    assert domain.pool_amounts_to_strategy(Decimal("1"), Decimal("2"), inverted=False) == (Decimal("1"), Decimal("2"))
    assert domain.strategy_price_to_pool(Decimal("123.45")) == Decimal("123.45")
    assert domain.pool_price_to_strategy(Decimal("123.45"), inverted=False) == Decimal("123.45")


def test_domain_inverts_amounts_and_bounds_when_pool_pair_is_reversed():
    # Strategy wants MEME as base and USDT as quote (PnL in USDT).
    # Pool is token0-token1 in the opposite order: USDT-MEME.
    domain = PoolDomainAdapter.from_config("MEME-USDT", "USDT-MEME")
    assert domain.pool_order_inverted is True
    assert domain.base_token == "MEME"
    assert domain.quote_token == "USDT"
    assert domain.pool_base_token == "USDT"
    assert domain.pool_quote_token == "MEME"

    # Amount mapping is a swap of (base, quote) <-> (quote, base)
    pool_base, pool_quote = domain.strategy_amounts_to_pool(Decimal("10"), Decimal("100"))
    assert pool_base == Decimal("100")
    assert pool_quote == Decimal("10")
    strat_base, strat_quote = domain.pool_amounts_to_strategy(pool_base, pool_quote, inverted=True)
    assert strat_base == Decimal("10")
    assert strat_quote == Decimal("100")

    # Price/bounds mapping is p -> 1/p, with bounds swapping.
    s_lower = Decimal("2")
    s_upper = Decimal("4")
    p_lower, p_upper = domain.strategy_bounds_to_pool(s_lower, s_upper)
    assert p_lower == Decimal("0.25")
    assert p_upper == Decimal("0.5")
    r_lower, r_upper = domain.pool_bounds_to_strategy(p_lower, p_upper, inverted=True)
    assert r_lower == s_lower
    assert r_upper == s_upper


def test_executor_token_order_inverted_detection_prefers_config_tokens():
    domain = PoolDomainAdapter.from_config("MEME-USDT", "USDT-MEME")
    executor = _DummyExecutor(_DummyExecutorConfig(base_token="USDT", quote_token="MEME", trading_pair="USDT-MEME"))
    assert domain.executor_token_order_inverted(executor) is True

