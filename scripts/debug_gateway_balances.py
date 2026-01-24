#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _read_simple_yaml_value(path: Path, key: str) -> Optional[str]:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(f"{key}:"):
            value = line.split(":", 1)[1].strip()
            if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                value = value[1:-1]
            return value or None
    return None


def _default_network(chain: str, repo_root: Path) -> Optional[str]:
    path = repo_root / "gateway-files" / "conf" / "chains" / f"{chain}.yml"
    return _read_simple_yaml_value(path, "defaultNetwork")


def _default_wallet(chain: str, repo_root: Path) -> Optional[str]:
    chain_cfg = repo_root / "gateway-files" / "conf" / "chains" / f"{chain}.yml"
    default_wallet = _read_simple_yaml_value(chain_cfg, "defaultWallet")
    if default_wallet:
        return default_wallet
    wallets_dir = repo_root / "gateway-files" / "conf" / "wallets" / chain
    if wallets_dir.exists():
        wallet_files = sorted(p for p in wallets_dir.iterdir() if p.suffix == ".json")
        if wallet_files:
            return wallet_files[0].stem
    return None


def _derive_tokens(trading_pair: str) -> List[str]:
    if "-" in trading_pair:
        parts = trading_pair.split("-")
    elif "_" in trading_pair:
        parts = trading_pair.split("_")
    else:
        parts = [trading_pair]
    return [p.strip() for p in parts if p.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Call Gateway /chains/<chain>/balances and print the raw response."
    )
    parser.add_argument("--gateway-url", default="http://localhost:15888")
    parser.add_argument("--chain", default="ethereum")
    parser.add_argument("--network")
    parser.add_argument("--address")
    parser.add_argument("--tokens", help="Comma-separated token symbols or addresses.")
    parser.add_argument("--trading-pair", help="Derive token list from BASE-QUOTE.")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    chain = args.chain
    network = args.network or _default_network(chain, repo_root)
    address = args.address or _default_wallet(chain, repo_root)

    if not network:
        print("Missing network. Pass --network or set gateway-files/conf/chains/<chain>.yml defaultNetwork.")
        return 2
    if not address:
        print("Missing address. Pass --address or set defaultWallet in gateway config.")
        return 2

    tokens: List[str] = []
    if args.tokens:
        tokens = [t.strip() for t in args.tokens.split(",") if t.strip()]
    elif args.trading_pair:
        tokens = _derive_tokens(args.trading_pair)

    payload = {"network": network, "address": address}
    if tokens:
        payload["tokens"] = tokens
    else:
        payload["tokens"] = []

    url = f"{args.gateway_url}/chains/{chain}/balances"
    req = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})

    print(f"POST {url}")
    print(json.dumps(payload, indent=2))
    start = time.time()
    try:
        with urlopen(req, timeout=args.timeout) as resp:
            body = resp.read().decode()
            elapsed = time.time() - start
            print(f"status={resp.status} elapsed={elapsed:.2f}s")
            print(body)
            return 0
    except HTTPError as err:
        elapsed = time.time() - start
        body = err.read().decode()
        print(f"status={err.code} elapsed={elapsed:.2f}s")
        print(body)
        return 1
    except URLError as err:
        elapsed = time.time() - start
        print(f"request failed elapsed={elapsed:.2f}s error={err}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
