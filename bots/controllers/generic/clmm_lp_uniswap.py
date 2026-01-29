import asyncio
from decimal import Decimal
from typing import Optional, Tuple

from pydantic import Field, field_validator

from . import clmm_lp_base
from .clmm_lp_domain.components import PoolDomainAdapter
from .clmm_lp_domain.clmm_fsm import CLMMFSM
from .clmm_lp_domain.io import ActionFactory, BalanceManager, SnapshotBuilder
from .clmm_lp_domain.policies import UniswapV3Policy
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.connector.utils import split_hb_trading_pair


class CLMMLPUniswapConfig(clmm_lp_base.CLMMLPBaseConfig):
    controller_name: str = "clmm_lp_uniswap"
    connector_name: str = "uniswap/clmm"
    router_connector: str = "uniswap/router"
    trading_pair: str = "ETH-USDC"
    pool_address: str = ""

    ratio_clamp_tick_multiplier: int = Field(default=2, json_schema_extra={"is_updatable": True})

    @field_validator("ratio_clamp_tick_multiplier", mode="after")
    @classmethod
    def validate_ratio_clamp_tick_multiplier(cls, v):
        if v is None or v <= 0:
            raise ValueError("ratio_clamp_tick_multiplier must be > 0")
        return v


class _UniswapPoolDomainResolver:
    def __init__(
        self,
        *,
        config: CLMMLPUniswapConfig,
        ctx,
        apply_domain,
        market_data_provider,
    ) -> None:
        self._config = config
        self._ctx = ctx
        self._apply_domain = apply_domain
        self._market_data_provider = market_data_provider
        self._task: Optional[asyncio.Task] = None
        self._last_attempt_ts: float = 0.0
        self._attempts: int = 0
        self._backoff_sec: float = 5.0
        self._enabled: bool = True

    def maybe_resolve(self, now: float) -> None:
        if not self._enabled:
            return
        if self._task is not None and not self._task.done():
            return
        if (now - self._last_attempt_ts) < self._backoff_sec:
            return
        self._last_attempt_ts = now
        self._task = safe_ensure_future(self._resolve())
        self._task.add_done_callback(self._clear_task)

    def _clear_task(self, task: asyncio.Task) -> None:
        if self._task is task:
            self._task = None

    async def _resolve(self) -> None:
        if not self._enabled:
            return
        pool_address = (self._config.pool_address or "").strip()
        if not pool_address:
            self._ctx.domain_ready = False
            self._ctx.domain_error = "missing_pool_address"
            self._enabled = False
            return

        pool_pair, error = await self._resolve_pool_trading_pair(pool_address)
        if error:
            self._ctx.domain_ready = False
            self._ctx.domain_error = error
            terminal_errors = {
                "missing_pool_address",
                "pool_not_found",
                "trading_pair_mismatch",
                "pool_data_invalid",
                "token_lookup_failed",
                "trading_pair_invalid",
            }
            if error in terminal_errors:
                self._enabled = False
                return
            self._attempts += 1
            backoff = 5.0 * (2 ** max(0, min(4, self._attempts - 1)))
            self._backoff_sec = min(60.0, backoff)
            return

        if pool_pair:
            self._config.pool_trading_pair = pool_pair
            domain = PoolDomainAdapter.from_config(self._config.trading_pair, pool_pair)
            self._apply_domain(domain)

        self._ctx.domain_ready = True
        self._ctx.domain_error = None
        self._ctx.domain_resolved_ts = self._market_data_provider.time()
        self._enabled = False
        self._attempts = 0
        self._backoff_sec = 5.0

    async def _resolve_pool_trading_pair(self, pool_address: str) -> Tuple[Optional[str], Optional[str]]:
        gateway = GatewayHttpClient.get_instance()
        chain, network, err = await gateway.get_connector_chain_network(self._config.connector_name)
        if err or not chain or not network:
            return None, "gateway_network_error"

        response = await gateway.api_request(
            "get",
            "pools/",
            params={
                "connector": "uniswap",
                "network": network,
                "type": "clmm",
                "search": pool_address,
            },
            fail_silently=True,
        )
        if response is None:
            return None, "pool_lookup_failed"
        if isinstance(response, dict):
            if response.get("error"):
                return None, "pool_lookup_failed"
            if response.get("message") and response.get("statusCode"):
                return None, "pool_lookup_failed"
        if not isinstance(response, list) or not response:
            return None, "pool_not_found"

        pool = response[0]
        base_addr = str(pool.get("baseTokenAddress") or "").lower()
        quote_addr = str(pool.get("quoteTokenAddress") or "").lower()
        if not base_addr or not quote_addr:
            return None, "pool_data_invalid"

        token0_addr, token1_addr = sorted([base_addr, quote_addr])
        token0 = await gateway.get_token(token0_addr, chain, network, fail_silently=True)
        token1 = await gateway.get_token(token1_addr, chain, network, fail_silently=True)
        token0_symbol = (token0.get("token") or {}).get("symbol") if isinstance(token0, dict) else None
        token1_symbol = (token1.get("token") or {}).get("symbol") if isinstance(token1, dict) else None
        if not token0_symbol or not token1_symbol:
            return None, "token_lookup_failed"

        pool_pair = f"{token0_symbol}-{token1_symbol}"
        try:
            strat_base, strat_quote = split_hb_trading_pair(self._config.trading_pair)
        except Exception:
            return None, "trading_pair_invalid"
        if {strat_base, strat_quote} != {token0_symbol, token1_symbol}:
            return None, "trading_pair_mismatch"
        return pool_pair, None


class CLMMLPUniswapController(clmm_lp_base.CLMMLPBaseController):
    def __init__(self, config: CLMMLPUniswapConfig, *args, **kwargs):
        domain = PoolDomainAdapter.from_config(config.trading_pair, config.pool_trading_pair)
        super().__init__(config, UniswapV3Policy(config, domain), domain, *args, **kwargs)
        self._domain_resolver: Optional[_UniswapPoolDomainResolver] = None
        if not config.pool_trading_pair:
            self._ctx.domain_ready = False
            self._ctx.domain_error = "awaiting_pool_order"
            self._domain_resolver = _UniswapPoolDomainResolver(
                config=config,
                ctx=self._ctx,
                apply_domain=self._apply_domain,
                market_data_provider=self.market_data_provider,
            )

    def _apply_domain(self, domain: PoolDomainAdapter) -> None:
        self._domain = domain
        if hasattr(self._policy, "_domain"):
            self._policy._domain = domain
        self._balance_manager = BalanceManager(
            config=self.config,
            domain=self._domain,
            market_data_provider=self.market_data_provider,
            logger=self.logger,
        )
        self._action_factory = ActionFactory(
            config=self.config,
            domain=self._domain,
            budget_key=self._budget_key,
            budget_coordinator=self._budget_coordinator,
            market_data_provider=self.market_data_provider,
            extra_lp_params=self._policy.extra_lp_params,
        )
        self._snapshot_builder = SnapshotBuilder(
            controller_id=self.config.id,
            domain=self._domain,
        )
        self._fsm = CLMMFSM(
            config=self.config,
            action_factory=self._action_factory,
            build_open_proposal=self._build_open_proposal,
            estimate_position_value=self._estimate_position_value,
            rebalance_engine=self._rebalance_engine,
            exit_policy=self._exit_policy,
        )

    async def update_processed_data(self):
        if self._domain_resolver is not None and not self._ctx.domain_ready:
            self._domain_resolver.maybe_resolve(self.market_data_provider.time())
        await super().update_processed_data()

    def determine_executor_actions(self):
        if not self._ctx.domain_ready:
            self._ctx.last_decision_reason = "domain_not_ready"
            return []
        return super().determine_executor_actions()
