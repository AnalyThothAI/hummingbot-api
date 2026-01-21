# Gateway Connector API Summary (Uniswap, PancakeSwap, Jupiter)

Source: `docs/gateway.json` (Hummingbot Gateway 2.11.0)
Server: `http://localhost:15888`

This document summarizes the swap-related endpoints for Uniswap, PancakeSwap, and Jupiter as defined in the local
OpenAPI spec. It focuses on router flows (quoteId-based) plus AMM and CLMM surfaces where applicable.

## Common Router Flow (Recommended)

1. `GET /connectors/{connector}/router/quote-swap` to obtain a quote and `quoteId`.
2. `POST /connectors/{connector}/router/execute-quote` with the `quoteId`.
3. Optional: `POST /connectors/{connector}/router/execute-swap` for one-shot quote+swap.

Router quote responses include `minAmountOut` and `maxAmountIn` and are the safest path for strict execution.

## Uniswap (connector: `uniswap`)

### Router
- `GET /connectors/uniswap/router/quote-swap`
  - query: `network` (default: `bsc`, enum: `arbitrum`, `avalanche`, `base`, `bsc`, `celo`, `mainnet`, `optimism`, `polygon`),
    `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`, `walletAddress`
  - response: `quoteId`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `priceImpactPct`,
    `minAmountOut`, `maxAmountIn`, `routePath`
- `POST /connectors/uniswap/router/execute-quote`
  - body: `walletAddress`, `network`, `quoteId`
  - response: `signature`, `status`, `data`
- `POST /connectors/uniswap/router/execute-swap`
  - body: `walletAddress`, `network`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
  - response: `signature`, `status`, `data`

### AMM
- `GET /connectors/uniswap/amm/quote-swap`
  - query: `network` (default: `base`), `poolAddress`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
  - response: `poolAddress`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `slippagePct`,
    `minAmountOut`, `maxAmountIn`, `priceImpactPct`
- `POST /connectors/uniswap/amm/execute-swap`
  - body: `walletAddress`, `network`, `poolAddress`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
  - response: `signature`, `status`, `data`
- `POST /connectors/uniswap/amm/add-liquidity`
  - body: `network`, `walletAddress`, `poolAddress`, `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`,
    `gasPrice`, `maxGas`
- `POST /connectors/uniswap/amm/remove-liquidity`
  - body: `network`, `walletAddress`, `poolAddress`, `percentageToRemove`, `gasPrice`, `maxGas`
- `GET /connectors/uniswap/amm/quote-liquidity`
  - query: `network`, `poolAddress`, `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`, `baseToken`, `quoteToken`
  - response: `baseLimited`, `baseTokenAmount`, `quoteTokenAmount`, `baseTokenAmountMax`, `quoteTokenAmountMax`
- `GET /connectors/uniswap/amm/pool-info`
  - query: `network`, `poolAddress`
  - response: `address`, `baseTokenAddress`, `quoteTokenAddress`, `feePct`, `price`,
    `baseTokenAmount`, `quoteTokenAmount`
- `GET /connectors/uniswap/amm/position-info`
  - query: `network`, `walletAddress`, `poolAddress`, `baseToken`, `quoteToken`
  - response: `poolAddress`, `walletAddress`, `baseTokenAddress`, `quoteTokenAddress`, `lpTokenAmount`,
    `baseTokenAmount`, `quoteTokenAmount`, `price`

### CLMM
- `GET /connectors/uniswap/clmm/quote-swap`
  - query: `network` (default: `base`), `poolAddress`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
  - response: `poolAddress`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `slippagePct`,
    `minAmountOut`, `maxAmountIn`, `priceImpactPct`
- `POST /connectors/uniswap/clmm/execute-swap`
  - body: `walletAddress`, `network`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
- `POST /connectors/uniswap/clmm/open-position`
  - body: `network`, `walletAddress`, `lowerPrice`, `upperPrice`, `poolAddress`,
    `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`
- `POST /connectors/uniswap/clmm/add-liquidity`
  - body: `network`, `walletAddress`, `positionAddress`, `baseTokenAmount`, `quoteTokenAmount`,
    `slippagePct`, `gasPrice`, `maxGas`
- `POST /connectors/uniswap/clmm/remove-liquidity`
  - body: `network`, `walletAddress`, `positionAddress`, `percentageToRemove`
- `POST /connectors/uniswap/clmm/close-position`
  - body: `network`, `walletAddress`, `positionAddress`
- `POST /connectors/uniswap/clmm/collect-fees`
  - body: `network`, `walletAddress`, `positionAddress`
- `GET /connectors/uniswap/clmm/quote-position`
  - query: `network`, `lowerPrice`, `upperPrice`, `poolAddress`,
    `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`
  - response: `baseLimited`, `baseTokenAmount`, `quoteTokenAmount`, `baseTokenAmountMax`, `quoteTokenAmountMax`, `liquidity`
- `GET /connectors/uniswap/clmm/positions-owned`
  - query: `network`, `walletAddress`
  - response: array of position objects (same fields as `position-info`)
- `GET /connectors/uniswap/clmm/pool-info`
  - query: `network`, `poolAddress`
  - response: `address`, `baseTokenAddress`, `quoteTokenAddress`, `binStep`, `feePct`, `price`,
    `baseTokenAmount`, `quoteTokenAmount`, `activeBinId`
- `GET /connectors/uniswap/clmm/position-info`
  - query: `network`, `positionAddress`
  - response: `address`, `poolAddress`, `baseTokenAddress`, `quoteTokenAddress`, `baseTokenAmount`, `quoteTokenAmount`,
    `baseFeeAmount`, `quoteFeeAmount`, `lowerBinId`, `upperBinId`, `lowerPrice`, `upperPrice`, `price`,
    `rewardTokenAddress`, `rewardAmount`

## PancakeSwap (connector: `pancakeswap`)

### Router
- `GET /connectors/pancakeswap/router/quote-swap`
  - query: `network` (default: `bsc`, enum: `arbitrum`, `base`, `bsc`, `mainnet`),
    `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`, `walletAddress`
  - response: `quoteId`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `priceImpactPct`,
    `minAmountOut`, `maxAmountIn`, `routePath`
- `POST /connectors/pancakeswap/router/execute-quote`
  - body: `walletAddress`, `network`, `quoteId`
- `POST /connectors/pancakeswap/router/execute-swap`
  - body: `walletAddress`, `network`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`

### AMM
- `GET /connectors/pancakeswap/amm/quote-swap`
  - query: `network` (default: `base`), `poolAddress`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
  - response: `poolAddress`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `slippagePct`,
    `minAmountOut`, `maxAmountIn`, `priceImpactPct`
- `POST /connectors/pancakeswap/amm/execute-swap`
  - body: `walletAddress`, `network`, `poolAddress`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
- `POST /connectors/pancakeswap/amm/add-liquidity`
  - body: `network`, `walletAddress`, `poolAddress`, `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`,
    `gasPrice`, `maxGas`
- `POST /connectors/pancakeswap/amm/remove-liquidity`
  - body: `network`, `walletAddress`, `poolAddress`, `percentageToRemove`, `gasPrice`, `maxGas`
- `GET /connectors/pancakeswap/amm/quote-liquidity`
  - query: `network`, `poolAddress`, `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`, `baseToken`, `quoteToken`
  - response: `baseLimited`, `baseTokenAmount`, `quoteTokenAmount`, `baseTokenAmountMax`, `quoteTokenAmountMax`
- `GET /connectors/pancakeswap/amm/pool-info`
  - query: `network`, `poolAddress`
  - response: `address`, `baseTokenAddress`, `quoteTokenAddress`, `feePct`, `price`,
    `baseTokenAmount`, `quoteTokenAmount`
- `GET /connectors/pancakeswap/amm/position-info`
  - query: `network`, `walletAddress`, `poolAddress`, `baseToken`, `quoteToken`
  - response: `poolAddress`, `walletAddress`, `baseTokenAddress`, `quoteTokenAddress`, `lpTokenAmount`,
    `baseTokenAmount`, `quoteTokenAmount`, `price`

### CLMM
- `GET /connectors/pancakeswap/clmm/quote-swap`
  - query: `network` (default: `bsc`), `poolAddress`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
  - response: `poolAddress`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `slippagePct`,
    `minAmountOut`, `maxAmountIn`, `priceImpactPct`
- `POST /connectors/pancakeswap/clmm/execute-swap`
  - body: `walletAddress`, `network`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`
- `POST /connectors/pancakeswap/clmm/open-position`
  - body: `network`, `walletAddress`, `lowerPrice`, `upperPrice`, `poolAddress`,
    `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`
- `POST /connectors/pancakeswap/clmm/add-liquidity`
  - body: `network`, `walletAddress`, `positionAddress`, `baseTokenAmount`, `quoteTokenAmount`,
    `slippagePct`, `gasPrice`, `maxGas`
- `POST /connectors/pancakeswap/clmm/remove-liquidity`
  - body: `network`, `walletAddress`, `positionAddress`, `percentageToRemove`
- `POST /connectors/pancakeswap/clmm/close-position`
  - body: `network`, `walletAddress`, `positionAddress`
- `POST /connectors/pancakeswap/clmm/collect-fees`
  - body: `network`, `walletAddress`, `positionAddress`
- `GET /connectors/pancakeswap/clmm/quote-position`
  - query: `network`, `lowerPrice`, `upperPrice`, `poolAddress`,
    `baseTokenAmount`, `quoteTokenAmount`, `slippagePct`
  - response: `baseLimited`, `baseTokenAmount`, `quoteTokenAmount`, `baseTokenAmountMax`, `quoteTokenAmountMax`, `liquidity`
- `GET /connectors/pancakeswap/clmm/positions-owned`
  - query: `network`, `walletAddress`
  - response: array of position objects (same fields as `position-info`)
- `GET /connectors/pancakeswap/clmm/pool-info`
  - query: `network`, `poolAddress`
  - response: `address`, `baseTokenAddress`, `quoteTokenAddress`, `binStep`, `feePct`, `price`,
    `baseTokenAmount`, `quoteTokenAmount`, `activeBinId`
- `GET /connectors/pancakeswap/clmm/position-info`
  - query: `network`, `positionAddress`
  - response: `address`, `poolAddress`, `baseTokenAddress`, `quoteTokenAddress`, `baseTokenAmount`, `quoteTokenAmount`,
    `baseFeeAmount`, `quoteFeeAmount`, `lowerBinId`, `upperBinId`, `lowerPrice`, `upperPrice`, `price`,
    `rewardTokenAddress`, `rewardAmount`

## Jupiter (connector: `jupiter`)

### Router
- `GET /connectors/jupiter/router/quote-swap`
  - query: `network` (default: `mainnet-beta`, enum: `devnet`, `mainnet-beta`),
    `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`,
    `restrictIntermediateTokens`, `onlyDirectRoutes`
  - response: `quoteId`, `tokenIn`, `tokenOut`, `amountIn`, `amountOut`, `price`, `priceImpactPct`,
    `minAmountOut`, `maxAmountIn`, `quoteResponse`, `approximation`
- `POST /connectors/jupiter/router/execute-quote`
  - body: `walletAddress`, `network`, `quoteId`, `priorityLevel`, `maxLamports`
  - response: `signature`, `status`, `data`
- `POST /connectors/jupiter/router/execute-swap`
  - body: `walletAddress`, `network`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`,
    `restrictIntermediateTokens`, `onlyDirectRoutes`, `priorityLevel`, `maxLamports`

## Unified Swap Endpoints

- `GET /trading/swap/quote`
  - query: `chainNetwork`, `connector` (default: `jupiter/router`), `baseToken`, `quoteToken`,
    `amount`, `side`, `slippagePct`
- `POST /trading/swap/execute`
  - body: `walletAddress`, `chainNetwork`, `connector`, `baseToken`, `quoteToken`, `amount`, `side`, `slippagePct`

## Runtime Availability

The OpenAPI spec documents endpoints and schemas. Actual runtime availability depends on Gateway configuration.
Use `GET /config/connectors` to confirm enabled connectors and supported networks.
