from typing import Dict


def normalize_search_pool(pool: Dict) -> Dict:
    return {
        "trading_pair": pool.get("trading_pair"),
        "base_symbol": pool.get("base_symbol"),
        "quote_symbol": pool.get("quote_symbol"),
        "base_address": pool.get("base_address"),
        "quote_address": pool.get("quote_address"),
        "fee_tier": pool.get("fee_tier"),
        "bin_step": pool.get("bin_step"),
        "volume_24h": pool.get("volume_24h"),
        "tvl_usd": pool.get("tvl_usd"),
        "apr": pool.get("apr"),
        "apy": pool.get("apy"),
        "address": pool.get("address"),
    }


def normalize_existing_pool(pool: Dict) -> Dict:
    base_symbol = pool.get("base") or pool.get("base_symbol")
    quote_symbol = pool.get("quote") or pool.get("quote_symbol")
    trading_pair = pool.get("trading_pair")
    if not trading_pair and base_symbol and quote_symbol:
        trading_pair = f"{base_symbol}-{quote_symbol}"
    return {
        "trading_pair": trading_pair,
        "base_symbol": base_symbol,
        "quote_symbol": quote_symbol,
        "base_address": pool.get("base_address") or pool.get("base_token_address"),
        "quote_address": pool.get("quote_address") or pool.get("quote_token_address"),
        "fee_tier": pool.get("fee_pct") if pool.get("fee_pct") is not None else pool.get("fee_tier"),
        "bin_step": pool.get("bin_step"),
        "volume_24h": None,
        "tvl_usd": None,
        "apr": None,
        "apy": None,
        "address": pool.get("address"),
    }
