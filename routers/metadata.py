from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import get_accounts_service
from services.accounts_service import AccountsService

router = APIRouter(tags=["Metadata"], prefix="/metadata")


def parse_chain_network(network_id: str) -> str:
    if not network_id or "-" not in network_id:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid network_id format. Expected 'chain-network', got '{network_id}'",
        )
    return network_id


def split_chain_network(network_id: str) -> tuple[str, str]:
    parts = network_id.split("-", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid network_id format. Expected 'chain-network', got '{network_id}'",
        )
    return parts[0], parts[1]


def get_field(payload: Dict, *keys):
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def to_decimal(value: Optional[object]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def decimal_to_str(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value.normalize(), "f")


def estimate_apr(volume_usd: Decimal, liquidity_usd: Decimal, fee_pct: Decimal) -> Optional[Decimal]:
    if liquidity_usd <= 0:
        return None
    daily_fees = volume_usd * fee_pct / Decimal("100")
    return (daily_fees / liquidity_usd) * Decimal("365") * Decimal("100")


def estimate_apy(apr_pct: Decimal) -> Decimal:
    daily_rate = apr_pct / Decimal("100") / Decimal("365")
    return ((Decimal("1") + daily_rate) ** 365 - Decimal("1")) * Decimal("100")


@router.get("/token")
async def get_token_metadata(
    network_id: str = Query(..., description="Chain and network in format: chain-network"),
    address: str = Query(..., description="Token contract address"),
    accounts_service: AccountsService = Depends(get_accounts_service),
) -> Dict:
    if not await accounts_service.gateway_client.ping():
        raise HTTPException(status_code=503, detail="Gateway service is not available")

    chain_network = parse_chain_network(network_id)
    chain, network = split_chain_network(chain_network)
    chain, network = split_chain_network(chain_network)

    result = await accounts_service.gateway_client.find_token(chain_network, address)
    if result is None:
        raise HTTPException(status_code=502, detail="Failed to fetch token metadata from Gateway")

    if isinstance(result, dict) and "error" in result:
        status = result.get("status", 502)
        raise HTTPException(status_code=status, detail=f"Gateway error: {result.get('error')}")

    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Unexpected Gateway response for token metadata")

    token_address = get_field(result, "address") or address
    symbol = get_field(result, "symbol")
    name = get_field(result, "name") or symbol
    decimals_raw = get_field(result, "decimals")
    try:
        decimals = int(decimals_raw) if decimals_raw is not None else None
    except (TypeError, ValueError):
        decimals = None

    warnings: List[str] = []
    if not symbol:
        warnings.append("symbol_missing")
    if decimals is None:
        warnings.append("decimals_missing")

    return {
        "ok": True,
        "source": "gateway",
        "token": {
            "network_id": chain_network,
            "address": token_address,
            "symbol": symbol,
            "name": name,
            "decimals": decimals,
        },
        "warnings": warnings,
    }


@router.get("/pools")
async def get_pools_metadata(
    network_id: str = Query(..., description="Chain and network in format: chain-network"),
    connector: Optional[str] = Query(default=None, description="DEX connector (e.g., uniswap, meteora)"),
    pool_type: Optional[str] = Query(default="clmm", description="Pool type (amm, clmm)"),
    token_a: Optional[str] = Query(default=None, description="Token A symbol or address"),
    token_b: Optional[str] = Query(default=None, description="Token B symbol or address"),
    search: Optional[str] = Query(default=None, description="Search by symbol/address or pair (e.g., SOL-USDC)"),
    pages: int = Query(default=1, ge=1, le=10, description="Pages to fetch from Gateway"),
    limit: int = Query(default=50, ge=1, le=200, description="Max results to return"),
    accounts_service: AccountsService = Depends(get_accounts_service),
) -> Dict:
    if not await accounts_service.gateway_client.ping():
        raise HTTPException(status_code=503, detail="Gateway service is not available")

    chain_network = parse_chain_network(network_id)

    token_a_value = token_a
    token_b_value = token_b

    if not token_a_value and not token_b_value and search:
        if "-" in search:
            parts = [part.strip() for part in search.split("-", 1)]
            token_a_value = parts[0] or None
            token_b_value = parts[1] if len(parts) > 1 else None
        else:
            token_a_value = search

    pools = await accounts_service.gateway_client.find_pools(
        chain_network,
        connector=connector,
        pool_type=pool_type,
        token_a=token_a_value,
        token_b=token_b_value,
        pages=pages,
    )

    warnings: List[str] = []
    pools_error = None
    pools_list: Optional[List[Dict]] = None
    if pools is None:
        pools_error = {"status": 502, "detail": "Failed to fetch pools from Gateway"}
    elif isinstance(pools, dict) and "error" in pools:
        pools_error = {"status": pools.get("status", 502), "detail": f"Gateway error: {pools.get('error')}"}
    elif not isinstance(pools, list):
        pools_error = {"status": 502, "detail": "Unexpected Gateway response for pools"}
    else:
        pools_list = pools

    meteora_pools = None
    if connector == "meteora" and chain == "solana":
        meteora_pools = await accounts_service.gateway_client.fetch_clmm_pools(
            connector="meteora",
            network=network,
            limit=limit,
            token_a=token_a_value,
            token_b=token_b_value,
        )
        if isinstance(meteora_pools, dict) and "error" in meteora_pools:
            warnings.append("meteora_fetch_failed")
            meteora_pools = None
        elif not isinstance(meteora_pools, list):
            meteora_pools = None

    if pools_list is None:
        if not meteora_pools:
            raise HTTPException(status_code=pools_error["status"], detail=pools_error["detail"])
        warnings.append("gecko_unavailable_fallback_meteora")
        pools_list = []

    meteora_by_address = {}
    if isinstance(meteora_pools, list):
        for item in meteora_pools:
            if isinstance(item, dict) and item.get("address"):
                meteora_by_address[item["address"]] = item

    normalized_pools = []
    for pool in pools_list:
        if not isinstance(pool, dict):
            continue

        meteora_info = None
        base_symbol = get_field(pool, "baseSymbol", "base_symbol")
        quote_symbol = get_field(pool, "quoteSymbol", "quote_symbol")
        base_address = get_field(pool, "baseTokenAddress", "base_token_address")
        quote_address = get_field(pool, "quoteTokenAddress", "quote_token_address")
        fee_pct_value = to_decimal(get_field(pool, "feePct", "fee_pct"))
        pool_address = get_field(pool, "address")
        pool_type_value = get_field(pool, "type", "pool_type") or pool_type
        gecko_data = get_field(pool, "geckoData", "gecko_data") or {}

        if pool_address and pool_address in meteora_by_address:
            meteora_info = meteora_by_address.get(pool_address)
            if base_address is None:
                base_address = get_field(meteora_info, "baseTokenAddress")
            if quote_address is None:
                quote_address = get_field(meteora_info, "quoteTokenAddress")
            if fee_pct_value is None:
                fee_pct_value = to_decimal(get_field(meteora_info, "feePct"))

        volume_usd = to_decimal(get_field(gecko_data, "volumeUsd24h", "volume_24h"))
        liquidity_usd = to_decimal(get_field(gecko_data, "liquidityUsd", "tvl_usd"))
        apr = to_decimal(get_field(gecko_data, "apr"))

        if apr is None and volume_usd is not None and liquidity_usd is not None and fee_pct_value is not None:
            apr = estimate_apr(volume_usd, liquidity_usd, fee_pct_value)

        apy = estimate_apy(apr) if apr is not None else None

        trading_pair = None
        if base_symbol and quote_symbol:
            trading_pair = f"{base_symbol}-{quote_symbol}"

        bin_step = None
        if meteora_info:
            bin_step = get_field(meteora_info, "binStep", "bin_step")

        normalized_pools.append({
            "address": pool_address,
            "trading_pair": trading_pair,
            "base_symbol": base_symbol,
            "quote_symbol": quote_symbol,
            "base_address": base_address,
            "quote_address": quote_address,
            "fee_tier": decimal_to_str(fee_pct_value),
            "bin_step": bin_step,
            "volume_24h": decimal_to_str(volume_usd),
            "tvl_usd": decimal_to_str(liquidity_usd),
            "apr": decimal_to_str(apr),
            "apy": decimal_to_str(apy),
            "pool_type": pool_type_value,
            "connector": connector,
            "network_id": chain_network,
        })

    if isinstance(meteora_pools, list):
        token_symbol_map: Dict[str, str] = {}
        if meteora_pools and not normalized_pools:
            tokens_response = await accounts_service.gateway_client.get_tokens(chain, network)
            if isinstance(tokens_response, dict):
                tokens_list = tokens_response.get("tokens", [])
                if isinstance(tokens_list, list):
                    token_symbol_map = {
                        token.get("address", "").lower(): token.get("symbol")
                        for token in tokens_list
                        if isinstance(token, dict) and token.get("address") and token.get("symbol")
                    }

        existing_addresses = {pool.get("address") for pool in normalized_pools if pool.get("address")}
        for pool in meteora_pools:
            if not isinstance(pool, dict):
                continue
            pool_address = get_field(pool, "address")
            if not pool_address or pool_address in existing_addresses:
                continue
            base_address = get_field(pool, "baseTokenAddress", "base_token_address")
            quote_address = get_field(pool, "quoteTokenAddress", "quote_token_address")
            fee_pct_value = to_decimal(get_field(pool, "feePct", "fee_pct"))
            bin_step = get_field(pool, "binStep", "bin_step")
            base_symbol = token_symbol_map.get(base_address.lower()) if base_address else None
            quote_symbol = token_symbol_map.get(quote_address.lower()) if quote_address else None
            trading_pair = None
            if base_symbol and quote_symbol:
                trading_pair = f"{base_symbol}-{quote_symbol}"

            normalized_pools.append({
                "address": pool_address,
                "trading_pair": trading_pair,
                "base_symbol": base_symbol,
                "quote_symbol": quote_symbol,
                "base_address": base_address,
                "quote_address": quote_address,
                "fee_tier": decimal_to_str(fee_pct_value),
                "bin_step": bin_step,
                "volume_24h": None,
                "tvl_usd": None,
                "apr": None,
                "apy": None,
                "pool_type": pool_type,
                "connector": connector,
                "network_id": chain_network,
            })

    if limit and len(normalized_pools) > limit:
        normalized_pools = normalized_pools[:limit]

    return {
        "ok": True,
        "source": "gateway",
        "total": len(normalized_pools),
        "pools": normalized_pools,
        "warnings": warnings,
    }
