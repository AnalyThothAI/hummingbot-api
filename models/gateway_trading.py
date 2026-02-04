"""
Models for Gateway DEX trading operations.
Supports unified swaps via Gateway trading/swap endpoints and CLMM liquidity positions.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from decimal import Decimal


# ============================================
# Swap Models (Router: Jupiter, 0x)
# ============================================

class SwapQuoteRequest(BaseModel):
    """Request for unified swap price quote (Gateway trading/swap)."""
    chain_network: str = Field(
        alias="chainNetwork",
        description="Chain and network (e.g., 'solana-mainnet-beta', 'ethereum-bsc')",
    )
    connector: Optional[str] = Field(
        default=None,
        description="Optional connector/type (e.g., 'jupiter/router', 'uniswap/clmm')",
    )
    base_token: str = Field(alias="baseToken", description="Symbol or address of the base token")
    quote_token: str = Field(alias="quoteToken", description="Symbol or address of the quote token")
    amount: Decimal = Field(description="Amount to swap")
    side: str = Field(description="Trade side: 'BUY' or 'SELL'")
    slippage_pct: Optional[Decimal] = Field(default=None, alias="slippagePct", description="Slippage percentage")

    class Config:
        allow_population_by_field_name = True


class SwapQuoteResponse(BaseModel):
    """Response with unified swap quote details (Gateway trading/swap)."""
    token_in: str = Field(alias="tokenIn", description="Address of the token being swapped from")
    token_out: str = Field(alias="tokenOut", description="Address of the token being swapped to")
    amount_in: Decimal = Field(alias="amountIn", description="Amount of tokenIn to be swapped")
    amount_out: Decimal = Field(alias="amountOut", description="Expected amount of tokenOut to receive")
    price: Decimal = Field(description="Exchange rate between tokenIn and tokenOut")
    price_impact_pct: Decimal = Field(alias="priceImpactPct", description="Estimated price impact percentage")
    min_amount_out: Decimal = Field(alias="minAmountOut", description="Minimum amount of tokenOut that will be accepted")
    max_amount_in: Decimal = Field(alias="maxAmountIn", description="Maximum amount of tokenIn that will be spent")
    pool_address: Optional[str] = Field(default=None, alias="poolAddress", description="Pool address (AMM/CLMM)")
    route_path: Optional[str] = Field(default=None, alias="routePath", description="Route path (router)")
    slippage_pct: Optional[Decimal] = Field(default=None, alias="slippagePct", description="Applied slippage percentage")

    class Config:
        allow_population_by_field_name = True


class SwapExecuteRequest(BaseModel):
    """Request to execute a unified swap (Gateway trading/swap)."""
    wallet_address: Optional[str] = Field(
        default=None,
        alias="walletAddress",
        description="Wallet address (optional, uses default if not provided)",
    )
    chain_network: str = Field(
        alias="chainNetwork",
        description="Chain and network (e.g., 'solana-mainnet-beta', 'ethereum-bsc')",
    )
    connector: Optional[str] = Field(
        default=None,
        description="Optional connector/type (e.g., 'jupiter/router', 'uniswap/clmm')",
    )
    base_token: str = Field(alias="baseToken", description="Symbol or address of the base token")
    quote_token: str = Field(alias="quoteToken", description="Symbol or address of the quote token")
    amount: Decimal = Field(description="Amount to swap")
    side: str = Field(description="Trade side: 'BUY' or 'SELL'")
    slippage_pct: Optional[Decimal] = Field(default=None, alias="slippagePct", description="Slippage percentage")

    class Config:
        allow_population_by_field_name = True


class SwapExecuteResponse(BaseModel):
    """Response after executing a unified swap (Gateway trading/swap)."""
    signature: str = Field(description="Transaction signature/hash")
    status: int = Field(description="Transaction status: 0 = PENDING, 1 = CONFIRMED, -1 = FAILED")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Optional execution details")

    class Config:
        allow_population_by_field_name = True


# ============================================
# CLMM Liquidity Models (Meteora, Raydium, Uniswap V3)
# ============================================

class CLMMOpenPositionRequest(BaseModel):
    """Request to open a new CLMM position with initial liquidity"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")

    # Position range
    lower_price: Decimal = Field(description="Lower price for position range")
    upper_price: Decimal = Field(description="Upper price for position range")

    # Initial liquidity
    base_token_amount: Optional[Decimal] = Field(default=None, description="Amount of base token to add")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")

    # Connector-specific parameters (e.g., strategyType for Meteora)
    extra_params: Optional[Dict[str, Any]] = Field(default=None, description="Additional connector-specific parameters")


class CLMMOpenPositionResponse(BaseModel):
    """Response after opening a new CLMM position"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: str = Field(description="Address of the newly created position")
    trading_pair: str = Field(description="Trading pair")
    pool_address: str = Field(description="Pool address")
    lower_price: Decimal = Field(description="Lower price bound")
    upper_price: Decimal = Field(description="Upper price bound")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMAddLiquidityRequest(BaseModel):
    """Request to add MORE liquidity to an EXISTING CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Existing position address to add liquidity to")
    base_token_amount: Optional[Decimal] = Field(default=None, description="Amount of base token to add")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMRemoveLiquidityRequest(BaseModel):
    """Request to remove SOME liquidity from a CLMM position (partial removal)"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to remove liquidity from")
    percentage: Decimal = Field(description="Percentage of liquidity to remove (0-100)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMClosePositionRequest(BaseModel):
    """Request to CLOSE a CLMM position completely (removes all liquidity and closes position)"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to close")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesRequest(BaseModel):
    """Request to collect fees from a CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to collect fees from")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesResponse(BaseModel):
    """Response after collecting fees"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: str = Field(description="Position address")
    base_fee_collected: Optional[Decimal] = Field(default=None, description="Base token fees collected")
    quote_fee_collected: Optional[Decimal] = Field(default=None, description="Quote token fees collected")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMPositionsOwnedRequest(BaseModel):
    """Request to get all CLMM positions owned by a wallet for a specific pool"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address to filter positions")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMPositionInfo(BaseModel):
    """Information about a CLMM liquidity position"""
    position_address: str = Field(description="Position address")
    pool_address: str = Field(description="Pool address")
    trading_pair: str = Field(description="Trading pair")
    base_token: str = Field(description="Base token symbol")
    quote_token: str = Field(description="Quote token symbol")
    base_token_amount: Decimal = Field(description="Base token amount in position")
    quote_token_amount: Decimal = Field(description="Quote token amount in position")
    current_price: Decimal = Field(description="Current pool price")
    lower_price: Decimal = Field(description="Lower price bound")
    upper_price: Decimal = Field(description="Upper price bound")
    base_fee_amount: Optional[Decimal] = Field(default=None, description="Base token uncollected fees")
    quote_fee_amount: Optional[Decimal] = Field(default=None, description="Quote token uncollected fees")
    lower_bin_id: Optional[int] = Field(default=None, description="Lower bin ID (Meteora)")
    upper_bin_id: Optional[int] = Field(default=None, description="Upper bin ID (Meteora)")
    in_range: bool = Field(description="Whether position is currently in range")


class CLMMGetPositionInfoRequest(BaseModel):
    """Request to get detailed info about a specific CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to query")


class CLMMPoolInfoRequest(BaseModel):
    """Request to get CLMM pool information by pool address"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")


class CLMMPoolBin(BaseModel):
    """Individual bin in a CLMM pool (e.g., Meteora)"""
    bin_id: int = Field(alias="binId", description="Bin identifier")
    price: Decimal = Field(description="Price at this bin")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Base token amount in bin")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Quote token amount in bin")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "bin_id": -374,
                "price": 0.47366592950616504,
                "base_token_amount": 19656.740028,
                "quote_token_amount": 18197.718539
            }
        }
    }


class CLMMPoolInfoResponse(BaseModel):
    """Response with detailed CLMM pool information"""
    address: str = Field(description="Pool address")
    base_token_address: str = Field(alias="baseTokenAddress", description="Base token contract address")
    quote_token_address: str = Field(alias="quoteTokenAddress", description="Quote token contract address")
    bin_step: Optional[int] = Field(None, alias="binStep", description="Bin step (Meteora DLMM only)")
    fee_pct: Decimal = Field(alias="feePct", description="Pool fee percentage")
    price: Decimal = Field(description="Current pool price")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Total base token liquidity")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Total quote token liquidity")
    active_bin_id: Optional[int] = Field(None, alias="activeBinId", description="Currently active bin ID (Meteora DLMM only)")
    dynamic_fee_pct: Optional[Decimal] = Field(None, alias="dynamicFeePct", description="Dynamic fee percentage")
    min_bin_id: Optional[int] = Field(None, alias="minBinId", description="Minimum bin ID (Meteora-specific)")
    max_bin_id: Optional[int] = Field(None, alias="maxBinId", description="Maximum bin ID (Meteora-specific)")
    bins: List[CLMMPoolBin] = Field(default_factory=list, description="List of bins with liquidity")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "address": "5hbf9JP8k5zdrZp9pokPypFQoBse5mGCmW6nqodurGcd",
                "base_token_address": "METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL",
                "quote_token_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "bin_step": 20,
                "fee_pct": 0.2,
                "price": 0.47366592950616504,
                "base_token_amount": 8645709.142366,
                "quote_token_amount": 1095942.335132,
                "active_bin_id": -374,
                "dynamic_fee_pct": 0.2,
                "min_bin_id": -21835,
                "max_bin_id": 21835,
                "bins": []
            }
        }
    }


# ============================================
# Pool Information Models
# ============================================

class GetPoolInfoRequest(BaseModel):
    """Request to get pool information"""
    connector: str = Field(description="DEX connector (e.g., 'meteora', 'raydium', 'jupiter')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    trading_pair: str = Field(description="Trading pair (e.g., 'SOL-USDC')")


class PoolInfo(BaseModel):
    """Information about a liquidity pool"""
    type: str = Field(description="Pool type: 'clmm' or 'router'")
    address: str = Field(description="Pool address")
    trading_pair: str = Field(description="Trading pair")
    base_token: str = Field(description="Base token symbol")
    quote_token: str = Field(description="Quote token symbol")
    current_price: Decimal = Field(description="Current pool price")
    base_token_amount: Decimal = Field(description="Base token liquidity in pool")
    quote_token_amount: Decimal = Field(description="Quote token liquidity in pool")
    fee_pct: Decimal = Field(description="Pool fee percentage")

    # CLMM-specific
    bin_step: Optional[int] = Field(default=None, description="Bin step (CLMM)")
    active_bin_id: Optional[int] = Field(default=None, description="Active bin ID (CLMM)")


# ============================================
# CLMM Pool Listing Models
# ============================================

class TimeBasedMetrics(BaseModel):
    """Time-based metrics (volume, fees, fee-to-TVL ratio) for different time periods"""
    min_30: Optional[Decimal] = Field(default=None, description="30 minute metric")
    hour_1: Optional[Decimal] = Field(default=None, description="1 hour metric")
    hour_2: Optional[Decimal] = Field(default=None, description="2 hour metric")
    hour_4: Optional[Decimal] = Field(default=None, description="4 hour metric")
    hour_12: Optional[Decimal] = Field(default=None, description="12 hour metric")
    hour_24: Optional[Decimal] = Field(default=None, description="24 hour metric")


class CLMMPoolListItem(BaseModel):
    """Individual pool item in CLMM pool listing"""
    address: str = Field(description="Pool address")
    name: str = Field(description="Pool name (e.g., 'SOL-USDC')")
    trading_pair: str = Field(description="Trading pair derived from tokens")
    mint_x: str = Field(description="Base token mint address")
    mint_y: str = Field(description="Quote token mint address")
    bin_step: int = Field(description="Bin step size")
    current_price: Decimal = Field(description="Current pool price")
    liquidity: str = Field(description="Total liquidity in pool")
    reserve_x: str = Field(description="Base token reserves")
    reserve_y: str = Field(description="Quote token reserves")
    reserve_x_amount: Optional[Decimal] = Field(default=None, description="Base token reserves as decimal amount")
    reserve_y_amount: Optional[Decimal] = Field(default=None, description="Quote token reserves as decimal amount")

    # Fee structure
    base_fee_percentage: Optional[str] = Field(default=None, description="Base fee percentage")
    max_fee_percentage: Optional[str] = Field(default=None, description="Maximum fee percentage")
    protocol_fee_percentage: Optional[str] = Field(default=None, description="Protocol fee percentage")

    # APR/APY
    apr: Optional[Decimal] = Field(default=None, description="Annual percentage rate")
    apy: Optional[Decimal] = Field(default=None, description="Annual percentage yield")
    farm_apr: Optional[Decimal] = Field(default=None, description="Farming annual percentage rate")
    farm_apy: Optional[Decimal] = Field(default=None, description="Farming annual percentage yield")

    # Volume and fees
    volume_24h: Optional[Decimal] = Field(default=None, description="24h trading volume")
    fees_24h: Optional[Decimal] = Field(default=None, description="24h fees collected")
    today_fees: Optional[Decimal] = Field(default=None, description="Today's fees collected")
    cumulative_trade_volume: Optional[str] = Field(default=None, description="Cumulative trade volume")
    cumulative_fee_volume: Optional[str] = Field(default=None, description="Cumulative fee volume")

    # Time-based metrics
    volume: Optional[TimeBasedMetrics] = Field(default=None, description="Volume across different time periods")
    fees: Optional[TimeBasedMetrics] = Field(default=None, description="Fees across different time periods")
    fee_tvl_ratio: Optional[TimeBasedMetrics] = Field(default=None, description="Fee-to-TVL ratio across different time periods")

    # Rewards
    reward_mint_x: Optional[str] = Field(default=None, description="Base token reward mint address")
    reward_mint_y: Optional[str] = Field(default=None, description="Quote token reward mint address")

    # Metadata
    tags: Optional[List[str]] = Field(default=None, description="Pool tags")
    is_verified: bool = Field(default=False, description="Whether tokens are verified")
    is_blacklisted: Optional[bool] = Field(default=None, description="Whether pool is blacklisted")
    hide: Optional[bool] = Field(default=None, description="Whether pool should be hidden")
    launchpad: Optional[str] = Field(default=None, description="Associated launchpad")


class CLMMPoolListResponse(BaseModel):
    """Response with list of available CLMM pools"""
    pools: List[CLMMPoolListItem] = Field(description="List of available pools")
    total: int = Field(description="Total number of pools")
    page: int = Field(description="Current page number")
    limit: int = Field(description="Results per page")
