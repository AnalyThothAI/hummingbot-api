import os
import sys
from decimal import Decimal


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


from bots.controllers.generic.clmm_lp_domain.components import PoolDomainAdapter


def test_pool_domain_adapter_detects_inversion_and_maps_amounts_prices_and_bounds():
    domain = PoolDomainAdapter.from_config(trading_pair="MEME-USDT", pool_trading_pair="USDT-MEME")
    assert domain.pool_order_inverted is True
    assert domain.base_token == "MEME"
    assert domain.quote_token == "USDT"
    assert domain.pool_base_token == "USDT"
    assert domain.pool_quote_token == "MEME"

    base_amt = Decimal("10")
    quote_amt = Decimal("100")
    pool_base, pool_quote = domain.strategy_amounts_to_pool(base_amt, quote_amt)
    assert pool_base == quote_amt
    assert pool_quote == base_amt

    strat_base, strat_quote = domain.pool_amounts_to_strategy(pool_base, pool_quote, inverted=True)
    assert strat_base == base_amt
    assert strat_quote == quote_amt

    # Strategy price: USDT per MEME. Pool price: MEME per USDT.
    strat_price = Decimal("0.04")
    pool_price = domain.strategy_price_to_pool(strat_price)
    assert pool_price == Decimal("25")
    assert domain.pool_price_to_strategy(pool_price, inverted=True) == strat_price

    # Use values that invert exactly in Decimal.
    lower = Decimal("0.04")
    upper = Decimal("0.08")
    pool_lower, pool_upper = domain.strategy_bounds_to_pool(lower, upper)
    assert pool_lower == Decimal("12.5")
    assert pool_upper == Decimal("25")
    mapped_lower, mapped_upper = domain.pool_bounds_to_strategy(pool_lower, pool_upper, inverted=True)
    assert mapped_lower == lower
    assert mapped_upper == upper


def test_pool_domain_adapter_identity_mapping_when_not_inverted():
    domain = PoolDomainAdapter.from_config(trading_pair="SOL-USDC", pool_trading_pair="SOL-USDC")
    assert domain.pool_order_inverted is False
    assert domain.strategy_amounts_to_pool(Decimal("1"), Decimal("2")) == (Decimal("1"), Decimal("2"))
    assert domain.strategy_price_to_pool(Decimal("10")) == Decimal("10")
    assert domain.strategy_bounds_to_pool(Decimal("9"), Decimal("11")) == (Decimal("9"), Decimal("11"))

