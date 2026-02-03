#!/usr/bin/env python3
import argparse
import math
import random
from decimal import Decimal, getcontext
from typing import List

getcontext().prec = 28


def d(x) -> Decimal:
    return Decimal(str(x))


def quote_per_base_ratio(price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if price <= 0 or lower <= 0 or upper <= 0 or lower >= upper:
        raise ValueError("invalid price or bounds")
    if not (lower < price < upper):
        raise ValueError("price must be within range for balanced amounts")
    sqrt_p = price.sqrt()
    sqrt_a = lower.sqrt()
    sqrt_b = upper.sqrt()
    denom = sqrt_b - sqrt_p
    numer = sqrt_p * sqrt_b * (sqrt_p - sqrt_a)
    if denom <= 0 or numer <= 0:
        raise ValueError("invalid sqrt math")
    return numer / denom


def target_amounts_from_value(value_quote: Decimal, price: Decimal, qpb: Decimal) -> (Decimal, Decimal):
    if value_quote <= 0 or price <= 0 or qpb <= 0:
        raise ValueError("invalid value/price/ratio")
    base = value_quote / (price + qpb)
    quote = value_quote - base * price
    if base <= 0 or quote < 0:
        raise ValueError("invalid target amounts")
    return base, quote


def liquidity_from_amount0(amount0: Decimal, sqrt_p: Decimal, sqrt_b: Decimal) -> Decimal:
    denom = (Decimal(1) / sqrt_p) - (Decimal(1) / sqrt_b)
    if denom <= 0:
        raise ValueError("invalid denom for amount0")
    return amount0 / denom


def liquidity_from_amount1(amount1: Decimal, sqrt_p: Decimal, sqrt_a: Decimal) -> Decimal:
    denom = sqrt_p - sqrt_a
    if denom <= 0:
        raise ValueError("invalid denom for amount1")
    return amount1 / denom


def amounts_from_liquidity(price: Decimal, lower: Decimal, upper: Decimal, liquidity: Decimal) -> (Decimal, Decimal):
    if price <= lower:
        amount0 = liquidity * ((Decimal(1) / lower.sqrt()) - (Decimal(1) / upper.sqrt()))
        amount1 = Decimal(0)
        return amount0, amount1
    if price >= upper:
        amount0 = Decimal(0)
        amount1 = liquidity * (upper.sqrt() - lower.sqrt())
        return amount0, amount1
    sqrt_p = price.sqrt()
    amount0 = liquidity * ((Decimal(1) / sqrt_p) - (Decimal(1) / upper.sqrt()))
    amount1 = liquidity * (sqrt_p - lower.sqrt())
    return amount0, amount1


def generate_prices_gbm(p0: float, steps: int, dt: float, drift: float, vol: float, seed: int) -> List[float]:
    random.seed(seed)
    prices = [p0]
    for _ in range(steps):
        z = random.gauss(0.0, 1.0)
        next_p = prices[-1] * math.exp((drift - 0.5 * vol ** 2) * dt + vol * math.sqrt(dt) * z)
        prices.append(next_p)
    return prices


def generate_prices_linear(p0: float, steps: int, p_end: float) -> List[float]:
    if steps <= 0:
        return [p0]
    step = (p_end - p0) / steps
    return [p0 + i * step for i in range(steps + 1)]


def parse_prices_list(prices: str) -> List[float]:
    return [float(x.strip()) for x in prices.split(",") if x.strip()]


def main():
    parser = argparse.ArgumentParser(description="Uniswap V3 LP PnL simulator (offline)")
    parser.add_argument("--p0", type=float, required=True, help="initial price (quote per base)")
    parser.add_argument("--lower", type=float, required=True, help="lower price bound")
    parser.add_argument("--upper", type=float, required=True, help="upper price bound")
    parser.add_argument("--value", type=float, required=True, help="initial position value in quote")
    parser.add_argument("--mode", type=str, default="gbm", choices=["gbm", "linear", "list"], help="price path mode")
    parser.add_argument("--steps", type=int, default=100, help="number of steps for gbm/linear")
    parser.add_argument("--dt", type=float, default=1.0, help="dt for gbm")
    parser.add_argument("--drift", type=float, default=0.0, help="drift for gbm")
    parser.add_argument("--vol", type=float, default=0.2, help="volatility for gbm")
    parser.add_argument("--seed", type=int, default=42, help="rng seed for gbm")
    parser.add_argument("--p_end", type=float, default=None, help="end price for linear")
    parser.add_argument("--prices", type=str, default=None, help="comma-separated prices for list mode")
    args = parser.parse_args()

    p0 = d(args.p0)
    lower = d(args.lower)
    upper = d(args.upper)
    value_quote = d(args.value)

    if lower <= 0 or upper <= 0 or lower >= upper:
        raise SystemExit("invalid bounds")
    if not (lower < p0 < upper):
        raise SystemExit("initial price must be within bounds for balanced entry")

    qpb = quote_per_base_ratio(p0, lower, upper)
    base0, quote0 = target_amounts_from_value(value_quote, p0, qpb)

    sqrt_p = p0.sqrt()
    sqrt_a = lower.sqrt()
    sqrt_b = upper.sqrt()
    L0 = liquidity_from_amount0(base0, sqrt_p, sqrt_b)
    L1 = liquidity_from_amount1(quote0, sqrt_p, sqrt_a)
    L = min(L0, L1)

    # Use actual amounts implied by L to avoid rounding mismatch
    base0_eff, quote0_eff = amounts_from_liquidity(p0, lower, upper, L)
    initial_value = base0_eff * p0 + quote0_eff

    if args.mode == "gbm":
        prices = generate_prices_gbm(args.p0, args.steps, args.dt, args.drift, args.vol, args.seed)
    elif args.mode == "linear":
        if args.p_end is None:
            raise SystemExit("--p_end required for linear mode")
        prices = generate_prices_linear(args.p0, args.steps, args.p_end)
    else:
        if not args.prices:
            raise SystemExit("--prices required for list mode")
        prices = parse_prices_list(args.prices)

    print("step,price,amount_base,amount_quote,value_quote,pnl_quote,pnl_pct")
    for i, p in enumerate(prices):
        price = d(p)
        amt0, amt1 = amounts_from_liquidity(price, lower, upper, L)
        value = (amt0 * price) + amt1
        pnl = value - initial_value
        pnl_pct = (pnl / initial_value) * d(100) if initial_value > 0 else d(0)
        print(",".join([
            str(i),
            f"{float(price):.8f}",
            f"{float(amt0):.8f}",
            f"{float(amt1):.8f}",
            f"{float(value):.8f}",
            f"{float(pnl):.8f}",
            f"{float(pnl_pct):.6f}",
        ]))


if __name__ == "__main__":
    main()
