# Gateway LP Root Cause Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Gateway-based LP bots start reliably and render correct prices/units in the dashboard by fixing the `GatewayLp has no attribute get_price_by_type` crash at its root, and by clarifying the gateway rebuild/recreate workflow.

**Architecture:** Apply a minimal Hummingbot core compatibility patch (MarketDataProvider gateway fallback) and ensure it is actually used by bot containers via a single-file bind mount from host. Then re-run the affected instance to verify the report pipeline and UI mapping behave correctly.

**Tech Stack:** Python (FastAPI, docker-py), Hummingbot (strategy_v2), Docker Compose, Streamlit dashboard.

---

### Task 1: Make `get_price_by_type` Compatible With Gateway Connectors (Persistent)

**Files:**
- Modify: `services/docker_service.py`
- Test: `test/services/test_docker_service_core_overrides.py`

**Step 1: Write failing unit test**
- Add a test that asserts DockerService computes a read-only bind mount for:
  - Host: `$BOTS_PATH/hummingbot/hummingbot/data_feed/market_data_provider.py`
  - Container: `/home/hummingbot/hummingbot/data_feed/market_data_provider.py`

**Step 2: Run test to verify it fails**
- Run: `./.venv/bin/python -m pytest -q test/services/test_docker_service_core_overrides.py`
- Expected: FAIL (override method missing or returns empty).

**Step 3: Implement minimal override helper + mount**
- Add a small helper on DockerService to build a `volumes` overlay dict.
- Update `create_hummingbot_instance()` to `volumes.update(overrides)` before `containers.run(...)`.

**Step 4: Re-run tests**
- Run: `./.venv/bin/python -m pytest -q test/services/test_docker_service_core_overrides.py`
- Expected: PASS.

**Step 5: Smoke check in Docker**
- Recreate the affected bot container (remove + deploy again) and confirm logs no longer show:
  - `AttributeError: 'GatewayLp' object has no attribute 'get_price_by_type'`

---

### Task 2: Verify Gateway Open Position BigInt Fix Survives Rebuild

**Files:**
- Verify: `gateway/src/connectors/uniswap/clmm-routes/openPosition.ts`
- Verify (built artifact): `gateway/dist/connectors/uniswap/clmm-routes/openPosition.js` (in running container)

**Step 1: Confirm source uses integer-safe raw amount conversion**
- Check for `toRawAmount()` usage for base/quote raw amounts.

**Step 2: Rebuild and recreate the gateway container**
- Run: `docker compose up -d --build --force-recreate gateway`

**Step 3: Confirm dist contains the fix**
- Grep inside container for `toRawAmount` in `openPosition.js`.

---

### Task 3: Dashboard Instances Page Validation (After Fix)

**Files:**
- Modify (if needed): `dashboard/frontend/pages/orchestration/instances/app.py`

**Step 1: Start bot and fetch status**
- Confirm controller `id` maps to config and quote symbol is detected (USDT, etc).

**Step 2: Validate UI units**
- Ensure no generic `Quote` placeholder is shown when trading pair is known.
- Ensure LP card shows:
  - base/quote amounts with correct symbols
  - fees displayed from `base_fee` + `quote_fee`

**Step 3: Only if still broken, implement a fallback**
- Infer quote symbol from performance payload when config mapping is missing.

---

### Task 4: Remove Agent Config Ambiguity (`@mcp` / `@skills`)

**Files:**
- Modify: `skills/mcp-bot-ops/SKILL.md`
- Modify (optional): `mcp/README.md`

**Step 1: Document `id` requirement**
- State: controller config should include `id`, and recommended `id == YAML basename`.

**Step 2: Document percent semantics**
- State: controller `*_pct` fields accept either ratio (`0.3`) or percent-points (`30`) and are normalized.
- State: gateway swap `slippagePct` remains 0-100, while controller config may accept 0.01 or 1 semantics depending on field.

---

### Verification Checklist

- `gateway` container healthy: `docker compose ps`
- Bot container logs: no `get_price_by_type` AttributeError
- Dashboard Instances page: quote symbol present, fees non-zero when gateway `position-info` returns non-zero fees
- Unit tests: `./.venv/bin/python -m pytest -q`

