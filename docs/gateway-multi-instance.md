# Gateway Multi-Chain Deployment Notes

This note captures the issues found when attempting multi-chain Gateway usage in a single Hummingbot-API stack,
the underlying reasons, recommended solutions, and maintenance trade-offs.

## Summary of Observed Issues

1) Connector config map 404 for gateway connectors
- Symptom: Credentials page shows `Could not get config map for jupiter/router: 404`.
- Cause: API route uses a path parameter that does not accept `/` in the connector name.
- Impact: Gateway connectors cannot be queried via `/connectors/{connector}/config-map`.
- Related code: `routers/connectors.py`, `dashboard/frontend/pages/orchestration/credentials/app.py`.

2) Gateway connectors do not expose config_keys
- Symptom: Even after routing fix, gateway connectors typically return an empty config map.
- Cause: Gateway connectors are dynamically registered without `config_keys` and are configured in Gateway, not in
  Hummingbot.
- Related code: `hummingbot/hummingbot/client/settings.py`, `hummingbot/hummingbot/core/gateway/gateway_http_client.py`.

3) Per-chain defaultNetwork is global, not per instance
- Symptom: Switching `defaultNetwork` affects all bots that rely on that chain.
- Cause: Gateway uses chain-level defaults (e.g., `gateway-files/conf/chains/ethereum.yml`), and Hummingbot core
  resolves chain/network via `get_connector_chain_network()` which reads the default network.
- Related code: `hummingbot/hummingbot/connector/gateway/gateway_base.py`,
  `hummingbot/hummingbot/core/gateway/gateway_http_client.py`.

4) Instance-level connector configs are not used by core
- Symptom: `_gateway_<connector>.yml` files in bot instance configs do not change gateway chain/network behavior.
- Cause: Hummingbot core reads `conf/connectors/*.yml` via connector config helpers and ignores files starting with `_`.
- Related code: `hummingbot/hummingbot/client/config/config_helpers.py`,
  `hummingbot/hummingbot/client/config/security.py`.

## Principles (Why This Happens)

- Gateway connectors are configured in Gateway, not in Hummingbot. Core expects chain/network defaults from Gateway.
- Connector names include a `/` (e.g., `jupiter/router`), which breaks simple path or file name handling unless
  explicitly accounted for.
- Hummingbot core does not currently support per-instance chain/network overrides for gateway connectors.

## Recommended Solution (Low-Maintenance)

Run one full stack per chain/network:
- One Gateway container per chain/network.
- One Hummingbot-API container per chain/network.
- Dedicated volumes for each stack (bots, gateway conf, logs, certs).
- Dedicated ports for each stack.

This avoids core changes and keeps upgrades aligned with upstream.

## Implementation Checklist (Per Chain)

- Use a separate compose project name (e.g., `hb-eth`, `hb-bsc`).
- Do not hardcode `container_name` in compose for multi-project deployments.
- Set unique ports for API, Gateway, Dashboard, Postgres, EMQX.
- Use per-chain volumes:
  - `bots-<chain>:/hummingbot-api/bots`
  - `gateway-files-<chain>:/home/gateway/conf`
  - `gateway-logs-<chain>:/home/gateway/logs`
  - `certs-<chain>:/home/gateway/certs`
- Set `GATEWAY_URL` in `.env` to the chain's Gateway endpoint.
- Configure `gateway-files-<chain>/conf/chains/<chain>.yml` with the desired `defaultNetwork`.

## Maintainability Notes

Low risk:
- No Hummingbot core fork required.
- Upgrades are just image bumps + restart per stack.
- Issues are isolated to a single chain stack.

Operational overhead:
- More containers (one stack per chain).
- Need a port allocation plan.
- Separate logs and credentials per chain.

## Potential Conflicts to Avoid

- Container name collisions: remove fixed `container_name` or ensure unique names per project.
- Port collisions: each stack must have its own API/Gateway/DB/EMQX ports.
- Volume collisions: never share `bots/` or `gateway-files/` between chains.
- MQTT topic collisions: sharing a broker across stacks can cause bot discovery collisions
  (`hbot/<bot_id>/...` topics are not namespaced by API).
- DB collisions: sharing a database without schema separation mixes bot state/history.

## Higher-Cost Alternative (Core Changes)

You can add per-instance gateway chain/network config in Hummingbot core, but this requires:
- Extending gateway connector configuration to accept chain/network/wallet.
- Wiring that config into connector initialization.
- Handling connector names containing `/` in config file paths.
- Updating market data providers to use instance-specific networks.

This path is higher risk and more expensive to maintain across upstream upgrades.
