#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  scripts/redeploy.sh [all|gateway|api|dashboard|hummingbot|verify] [--dev] [--no-build] [--no-recreate] [--no-cache] [--hummingbot-image <tag>]

What it does:
  - Rebuilds and/or recreates Docker Compose services so your code changes take effect.
  - Optionally builds a local Hummingbot bot image from ./hummingbot (git submodule).
  - Optionally runs lightweight HTTP smoke checks.

Flags:
  --dev         Use docker-compose.dev.yml overlay (only affects hummingbot-api hot-reload mounts).
  --no-build    Skip image build step (restart/recreate only).
  --no-recreate Skip --force-recreate (let Compose decide).
  --no-cache    Disable Docker build cache (applies to docker-compose builds and the hummingbot image build).
  --hummingbot-image <tag>  Tag to use for the local Hummingbot image (default: hummingbot/hummingbot:local).

Examples:
  scripts/redeploy.sh gateway
  scripts/redeploy.sh all
  scripts/redeploy.sh api --dev
  scripts/redeploy.sh hummingbot --no-cache
  scripts/redeploy.sh verify

Notes:
  - This script does NOT restart bot instance containers, because a restart can trigger on-chain actions.
  - After building the local Hummingbot image, select it in the dashboard "Hummingbot Image" dropdown
    (e.g. hummingbot/hummingbot:local) when deploying a new instance.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

compose() {
  local -a files
  files=(-f docker-compose.yml)
  if [[ "${DEV_MODE:-0}" == "1" ]]; then
    files+=(-f docker-compose.dev.yml)
  fi
  docker compose "${files[@]}" "$@"
}

build_hummingbot_image() {
  local image="${HUMMINGBOT_IMAGE:-hummingbot/hummingbot:local}"
  local context="$ROOT_DIR/hummingbot"
  local dockerfile="$context/Dockerfile"

  [[ -f "$dockerfile" ]] || die "Missing $dockerfile (is the ./hummingbot submodule initialized?)"

  echo "build(hummingbot): $image"
  local -a args
  args=(build -t "$image" -f "$dockerfile")
  if [[ "${NO_CACHE:-0}" == "1" ]]; then
    args+=(--no-cache)
  fi
  # build_ext can be memory-hungry; allow overriding parallelism.
  args+=(--build-arg "BUILD_EXT_JOBS=${HB_BUILD_EXT_JOBS:-2}")
  args+=("$context")
  docker "${args[@]}"
}

wait_http() {
  local url="$1"
  local timeout_sec="${2:-60}"
  local start
  start="$(date +%s)"
  while true; do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    if (( "$(date +%s)" - start >= timeout_sec )); then
      die "Timed out waiting for HTTP: $url"
    fi
    sleep 1
  done
}

redeploy_service() {
  local service="$1"
  local -a args
  args=(up -d)
  if [[ "${NO_BUILD:-0}" != "1" ]]; then
    if [[ "${NO_CACHE:-0}" == "1" ]]; then
      compose build --no-cache "$service"
    else
      args+=(--build)
    fi
  fi
  if [[ "${NO_RECREATE:-0}" != "1" ]]; then
    args+=(--force-recreate)
  fi
  args+=("$service")
  compose "${args[@]}"
}

verify_gateway_fees() {
  local eth_cfg="$ROOT_DIR/gateway-files/conf/chains/ethereum.yml"
  if [[ ! -f "$eth_cfg" ]]; then
    echo "verify(gateway): skip fee check (missing $eth_cfg)"
    return 0
  fi

  local network wallet
  network="$(awk -F': ' '/^defaultNetwork:/{print $2}' "$eth_cfg" | tr -d " '\r\n")"
  wallet="$(awk -F': ' '/^defaultWallet:/{print $2}' "$eth_cfg" | tr -d " '\r\n")"
  if [[ -z "$network" || -z "$wallet" ]]; then
    echo "verify(gateway): skip fee check (could not parse defaultNetwork/defaultWallet from $eth_cfg)"
    return 0
  fi

  local tmp_positions tmp_info
  tmp_positions="$(mktemp)"
  tmp_info="$(mktemp)"
  # Use literal paths in the trap to avoid nounset issues with local vars on RETURN.
  trap "rm -f -- '$tmp_positions' '$tmp_info'" RETURN

  local position_id=""
  local attempts=0
  while [[ -z "$position_id" && "$attempts" -lt 3 ]]; do
    attempts=$((attempts + 1))
    curl -fsS --max-time 10 \
      "http://localhost:15888/connectors/uniswap/clmm/positions-owned?network=${network}&walletAddress=${wallet}" \
      >"$tmp_positions"

    position_id="$(python3 - "$tmp_positions" <<'PY'
import json, sys
positions = json.load(open(sys.argv[1]))
if not positions:
  sys.exit(0)
def score(p):
  try:
    return abs(float(p.get("baseFeeAmount") or 0)) + abs(float(p.get("quoteFeeAmount") or 0))
  except Exception:
    return 0.0
best = max(positions, key=score)
print(best.get("address", ""), end="")
PY
)"

    if [[ -z "$position_id" ]]; then
      sleep 2
    fi
  done

  if [[ -z "$position_id" ]]; then
    echo "verify(gateway): positions-owned returned 0 positions for wallet=$wallet network=$network (after ${attempts} attempts)"
    return 0
  fi

  curl -fsS --max-time 10 \
    "http://localhost:15888/connectors/uniswap/clmm/position-info?network=${network}&positionAddress=${position_id}&walletAddress=${wallet}" \
    >"$tmp_info"

  python3 - "$tmp_positions" "$tmp_info" <<'PY'
import json, math, sys
positions = json.load(open(sys.argv[1]))
info = json.load(open(sys.argv[2]))
target_id = str(info.get("address", ""))
pos = next((p for p in positions if str(p.get("address", "")) == target_id), positions[0])

def f(v):
  try:
    return float(v)
  except Exception:
    return 0.0

owned_base = f(pos.get("baseFeeAmount"))
owned_quote = f(pos.get("quoteFeeAmount"))
info_base = f(info.get("baseFeeAmount"))
info_quote = f(info.get("quoteFeeAmount"))

diff_base = abs(owned_base - info_base)
diff_quote = abs(owned_quote - info_quote)

print(f"verify(gateway): sample position {pos.get('address')} fees_owned(base={owned_base}, quote={owned_quote}) "
      f"fees_info(base={info_base}, quote={info_quote}) diffs(base={diff_base}, quote={diff_quote})")
PY
}

verify() {
  echo "verify: gateway http://localhost:15888/"
  wait_http "http://localhost:15888/" 60

  echo "verify: hummingbot-api http://localhost:18000/"
  wait_http "http://localhost:18000/" 60

  echo "verify: dashboard http://localhost:8502/"
  wait_http "http://localhost:8502/" 90

  verify_gateway_fees
}

main() {
  need_cmd docker
  need_cmd curl
  need_cmd python3

  # Allow global help before the target (e.g. `scripts/redeploy.sh --help`).
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  local target="${1:-}"
  shift || true

  DEV_MODE=0
  NO_BUILD=0
  NO_RECREATE=0
  NO_CACHE=0
  HUMMINGBOT_IMAGE="hummingbot/hummingbot:local"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dev)
        DEV_MODE=1
        ;;
      --no-build)
        NO_BUILD=1
        ;;
      --no-recreate)
        NO_RECREATE=1
        ;;
      --no-cache)
        NO_CACHE=1
        ;;
      --hummingbot-image)
        shift || true
        [[ -n "${1:-}" ]] || die "--hummingbot-image requires a tag value"
        HUMMINGBOT_IMAGE="$1"
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown arg: $1"
        ;;
    esac
    shift
  done

  case "$target" in
    gateway)
      redeploy_service gateway
      # Give the container a moment to finish booting before running HTTP checks.
      wait_http "http://localhost:15888/" 60
      verify_gateway_fees || true
      ;;
    api|hummingbot-api)
      redeploy_service hummingbot-api
      ;;
    dashboard)
      redeploy_service dashboard
      ;;
    hummingbot)
      build_hummingbot_image
      ;;
    all)
      # Keep dependencies stable; only rebuild app-facing services by default.
      redeploy_service gateway
      redeploy_service hummingbot-api
      redeploy_service dashboard
      verify
      ;;
    verify)
      verify
      ;;
    "")
      usage
      exit 1
      ;;
    *)
      usage
      die "Unknown target: $target"
      ;;
  esac
}

main "$@"
