import asyncio
from decimal import Decimal
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

from .components import LPView, PoolDomainAdapter, PriceContext, Snapshot, SwapPurpose, SwapView

if TYPE_CHECKING:
    from ..clmm_lp_base import CLMMLPBaseConfig
    from .components import OpenProposal


class SnapshotBuilder:
    def __init__(
        self,
        *,
        controller_id: str,
        domain: PoolDomainAdapter,
    ) -> None:
        self._controller_id = controller_id
        self._domain = domain

    def build(
        self,
        *,
        now: float,
        current_price: Optional[Decimal],
        executors_info: List[ExecutorInfo],
        wallet_base: Decimal,
        wallet_quote: Decimal,
        balance_fresh: bool = False,
    ) -> Snapshot:
        lp: Dict[str, LPView] = {}
        swaps: Dict[str, SwapView] = {}
        for executor in executors_info:
            if executor.controller_id != self._controller_id:
                continue
            if executor.type == "lp_position_executor":
                lp[executor.id] = self._parse_lp_view(executor)
            elif executor.type == "gateway_swap_executor":
                level_id = getattr(executor.config, "level_id", None)
                swaps[executor.id] = SwapView(
                    executor_id=executor.id,
                    is_active=executor.is_active,
                    is_done=executor.is_done,
                    close_type=executor.close_type,
                    timestamp=float(executor.timestamp or 0),
                    level_id=level_id,
                    purpose=self._swap_purpose(level_id),
                    amount=Decimal(str(getattr(executor.config, "amount", 0))),
                )

        active_lp = [v for v in lp.values() if v.is_active]
        active_swaps = [v for v in swaps.values() if v.is_active]
        return Snapshot(
            now=now,
            current_price=current_price,
            balance_fresh=balance_fresh,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            lp=lp,
            swaps=swaps,
            active_lp=active_lp,
            active_swaps=active_swaps,
        )

    def _parse_lp_view(self, executor: ExecutorInfo) -> LPView:
        custom = executor.custom_info or {}
        lp_base_amount = Decimal(str(custom.get("base_amount", 0)))
        lp_quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        lp_base_fee = Decimal(str(custom.get("base_fee", 0)))
        lp_quote_fee = Decimal(str(custom.get("quote_fee", 0)))

        inverted = self._domain.executor_token_order_inverted(executor)
        if inverted is None:
            inverted = self._domain.pool_order_inverted
        base_amount, quote_amount = self._domain.pool_amounts_to_strategy(lp_base_amount, lp_quote_amount, inverted)
        base_fee, quote_fee = self._domain.pool_amounts_to_strategy(lp_base_fee, lp_quote_fee, inverted)

        lower = custom.get("lower_price")
        upper = custom.get("upper_price")
        lower = Decimal(str(lower)) if lower is not None else None
        upper = Decimal(str(upper)) if upper is not None else None
        if lower is not None and upper is not None:
            lower, upper = self._domain.pool_bounds_to_strategy(lower, upper, inverted)
        out_of_range_since = custom.get("out_of_range_since")
        out_of_range_since = float(out_of_range_since) if out_of_range_since is not None else None

        return LPView(
            executor_id=executor.id,
            is_active=executor.is_active,
            is_done=executor.is_done,
            close_type=executor.close_type,
            state=custom.get("state"),
            position_address=custom.get("position_address"),
            base_amount=base_amount,
            quote_amount=quote_amount,
            base_fee=base_fee,
            quote_fee=quote_fee,
            lower_price=lower,
            upper_price=upper,
            out_of_range_since=out_of_range_since,
        )

    @staticmethod
    def _swap_purpose(level_id: Optional[str]) -> Optional[SwapPurpose]:
        if level_id == SwapPurpose.INVENTORY.value:
            return SwapPurpose.INVENTORY
        if level_id == SwapPurpose.INVENTORY_REBALANCE.value:
            return SwapPurpose.INVENTORY_REBALANCE
        if level_id == SwapPurpose.STOPLOSS.value:
            return SwapPurpose.STOPLOSS
        return None


class BalanceManager:
    def __init__(
        self,
        *,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        market_data_provider,
        logger: Callable[[], HummingbotLogger],
    ) -> None:
        self._config = config
        self._domain = domain
        self._market_data_provider = market_data_provider
        self._logger = logger

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")
        self._wallet_source: Optional[str] = None
        self._last_balance_update_ts: float = 0.0
        self._last_balance_attempt_ts: float = 0.0
        self._wallet_update_task: Optional[asyncio.Task] = None
        self._has_balance_snapshot: bool = False
        self._logged_balance_snapshot: bool = False

    @property
    def wallet_base(self) -> Decimal:
        return self._wallet_base

    @property
    def wallet_quote(self) -> Decimal:
        return self._wallet_quote

    @property
    def wallet_source(self) -> Optional[str]:
        return self._wallet_source

    @property
    def has_snapshot(self) -> bool:
        return self._has_balance_snapshot

    def schedule_refresh(self, now: float, force: bool = False) -> None:
        if self._wallet_update_task is not None and not self._wallet_update_task.done():
            return
        if (now - self._last_balance_attempt_ts) < 1.0:
            return
        stale = self._has_balance_snapshot and not self.is_fresh(now)
        if not force:
            if self._has_balance_snapshot and not stale:
                return
            if stale:
                min_interval = float(max(1, self._config.balance_refresh_timeout_sec))
                if (now - self._last_balance_attempt_ts) < min_interval:
                    return

        primary_name = self._config.router_connector or self._config.connector_name
        connector = self._market_data_provider.connectors.get(primary_name)
        if connector is None:
            return
        self._last_balance_attempt_ts = now
        self._wallet_update_task = safe_ensure_future(self._update_wallet_balances())
        self._wallet_update_task.add_done_callback(self._clear_wallet_update_task)

    def is_fresh(self, now: float) -> bool:
        if not self._has_balance_snapshot:
            return False
        timeout = max(0, self._config.balance_refresh_timeout_sec)
        if timeout <= 0:
            return True
        return (now - self._last_balance_update_ts) <= timeout

    def _clear_wallet_update_task(self, task: asyncio.Task) -> None:
        if self._wallet_update_task is task:
            self._wallet_update_task = None

    async def _update_wallet_balances(self) -> None:
        timeout = float(max(1, self._config.balance_update_timeout_sec))
        router_name = self._config.router_connector
        primary_name = router_name or self._config.connector_name
        primary_connector = self._market_data_provider.connectors.get(primary_name)
        token_symbols = [self._domain.base_token, self._domain.quote_token]
        if self._config.native_token_symbol:
            token_symbols.append(self._config.native_token_symbol)

        async def _safe_update(conn, name: str) -> bool:
            if conn is None:
                return False
            try:
                await asyncio.wait_for(conn.update_balances(token_symbols=token_symbols), timeout=timeout)
                return True
            except Exception:
                self._logger().exception(
                    "update_balances failed | connector=%s base=%s quote=%s last_update_ts=%.0f last_attempt_ts=%.0f",
                    name,
                    self._domain.base_token,
                    self._domain.quote_token,
                    self._last_balance_update_ts,
                    self._last_balance_attempt_ts,
                )
                return False

        if primary_connector is None:
            return
        if not await _safe_update(primary_connector, primary_name):
            return

        available_balances = getattr(primary_connector, "available_balances", None)
        base_present = True
        quote_present = True
        if isinstance(available_balances, dict):
            base_present = self._domain.base_token in available_balances
            quote_present = self._domain.quote_token in available_balances

        new_wallet_base = Decimal(str(primary_connector.get_available_balance(self._domain.base_token) or 0))
        new_wallet_quote = Decimal(str(primary_connector.get_available_balance(self._domain.quote_token) or 0))
        if not base_present or not quote_present:
            missing_tokens = []
            if not base_present:
                missing_tokens.append(self._domain.base_token)
            if not quote_present:
                missing_tokens.append(self._domain.quote_token)
            if self._has_balance_snapshot and (
                (not base_present and self._wallet_base > 0) or (not quote_present and self._wallet_quote > 0)
            ):
                self._logger().warning(
                    "balance_snapshot_ignored | source=%s missing=%s last_base=%s last_quote=%s",
                    primary_name,
                    ",".join(missing_tokens),
                    self._wallet_base,
                    self._wallet_quote,
                )
                return

        self._wallet_base = new_wallet_base
        self._wallet_quote = new_wallet_quote
        self._wallet_source = primary_name
        self._last_balance_update_ts = self._market_data_provider.time()
        self._has_balance_snapshot = True
        if not self._logged_balance_snapshot:
            self._logger().info(
                "balance_snapshot_ready | source=%s base=%s quote=%s",
                self._wallet_source,
                self._wallet_base,
                self._wallet_quote,
            )
            self._logged_balance_snapshot = True


class PriceProvider:
    def __init__(
        self,
        *,
        connector_name: str,
        trading_pair: str,
        market_data_provider,
        logger: Callable[[], HummingbotLogger],
        poll_interval_sec: float = 15.0,
        retry_interval_sec: float = 5.0,
        ttl_sec: float = 20.0,
    ) -> None:
        self._connector_name = connector_name
        self._trading_pair = trading_pair
        self._market_data_provider = market_data_provider
        self._logger = logger
        self._poll_interval_sec = poll_interval_sec
        self._retry_interval_sec = retry_interval_sec
        self._ttl_sec = ttl_sec

        self._price_ctx: Optional[PriceContext] = None
        self._price_task: Optional[asyncio.Task] = None
        self._last_attempt_ts: float = 0.0
        self._chain: Optional[str] = None
        self._network: Optional[str] = None

    def stop(self) -> None:
        if self._price_task is not None and not self._price_task.done():
            self._price_task.cancel()
        self._price_task = None

    def get_price_context(self, now: float) -> PriceContext:
        self._schedule_refresh(now)
        if self._is_fresh(now):
            ctx = self._price_ctx
            return PriceContext(value=ctx.value, source=ctx.source, timestamp=ctx.timestamp)
        return PriceContext(value=None, source="unavailable", timestamp=0.0)

    def _is_fresh(self, now: float) -> bool:
        ctx = self._price_ctx
        if ctx is None or ctx.value is None or ctx.value <= 0:
            return False
        return (now - ctx.timestamp) <= self._ttl_sec

    def _schedule_refresh(self, now: float) -> None:
        if self._price_task is not None and not self._price_task.done():
            return
        ctx = self._price_ctx
        if ctx is None or ctx.timestamp <= 0:
            if (now - self._last_attempt_ts) < self._retry_interval_sec:
                return
        else:
            if (now - ctx.timestamp) < self._poll_interval_sec:
                return
        self._last_attempt_ts = now
        self._price_task = safe_ensure_future(self._refresh_price())
        self._price_task.add_done_callback(self._clear_price_task)

    async def _refresh_price(self) -> None:
        connector = self._connector_name
        if not connector:
            return
        if self._chain is None or self._network is None:
            chain, network, error = await GatewayHttpClient.get_instance().get_connector_chain_network(connector)
            if error:
                self._logger().warning("price_refresh_failed | connector=%s error=%s", connector, error)
                return
            self._chain = chain
            self._network = network

        base, quote = split_hb_trading_pair(trading_pair=self._trading_pair)
        last_error = None
        for attempt in range(2):
            try:
                response = await GatewayHttpClient.get_instance().get_price(
                    chain=self._chain,
                    network=self._network,
                    connector=connector,
                    base_asset=base,
                    quote_asset=quote,
                    amount=Decimal("1"),
                    side=TradeType.SELL,
                )
                price = None
                if response and "price" in response:
                    price = Decimal(str(response["price"]))
                if price is not None and price > 0:
                    now = self._market_data_provider.time()
                    self._price_ctx = PriceContext(
                        value=price,
                        source=f"gateway_direct:{connector}",
                        timestamp=now,
                    )
                    return
            except Exception as exc:
                last_error = exc
            if attempt == 0:
                await asyncio.sleep(self._retry_interval_sec)
        if last_error is not None:
            self._logger().warning("price_refresh_failed | connector=%s error=%s", connector, last_error)

    def _clear_price_task(self, task: asyncio.Task) -> None:
        if self._price_task is task:
            self._price_task = None


class ActionFactory:
    def __init__(
        self,
        *,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        budget_key: str,
        budget_coordinator,
        market_data_provider,
        extra_lp_params: Callable[[], Optional[Dict]],
    ) -> None:
        self._config = config
        self._domain = domain
        self._budget_key = budget_key
        self._budget_coordinator = budget_coordinator
        self._market_data_provider = market_data_provider
        self._extra_lp_params = extra_lp_params

    def build_open_lp_action(self, proposal: "OpenProposal", now: float) -> Optional[CreateExecutorAction]:
        executor_config = self._create_lp_executor_config(proposal, now)
        if executor_config is None:
            return None
        return CreateExecutorAction(controller_id=self._config.id, executor_config=executor_config)

    def swap_slippage_pct(self) -> Decimal:
        return max(Decimal("0"), self._config.swap_slippage_pct) * Decimal("100")

    def build_swap_action(
        self,
        *,
        level_id: str,
        now: float,
        side: TradeType,
        amount: Decimal,
        amount_in_is_quote: bool,
        apply_buffer: bool,
    ) -> Optional[CreateExecutorAction]:
        if amount <= 0:
            return None
        if apply_buffer:
            amount = self._apply_swap_buffer(amount)
            if amount <= 0:
                return None
        executor_config = GatewaySwapExecutorConfig(
            timestamp=now,
            connector_name=self._config.router_connector,
            trading_pair=self._config.trading_pair,
            side=side,
            amount=amount,
            amount_in_is_quote=amount_in_is_quote,
            slippage_pct=self.swap_slippage_pct(),
            pool_address=self._config.pool_address or None,
            level_id=level_id,
            budget_key=self._budget_key,
        )
        return CreateExecutorAction(
            controller_id=self._config.id,
            executor_config=executor_config,
        )

    def _apply_swap_buffer(self, amount: Decimal) -> Decimal:
        buffer_pct = max(Decimal("0"), self._config.swap_safety_buffer_pct)
        if buffer_pct <= 0:
            return amount
        if buffer_pct >= 1:
            return Decimal("0")
        return amount * (Decimal("1") - buffer_pct)

    def _create_lp_executor_config(self, proposal: "OpenProposal", now: float) -> Optional[LPPositionExecutorConfig]:
        if proposal.open_base <= 0 or proposal.open_quote <= 0:
            return None
        lower_price, upper_price = proposal.lower, proposal.upper
        lp_base_amt, lp_quote_amt = self._domain.strategy_amounts_to_pool(
            proposal.open_base,
            proposal.open_quote,
        )
        lp_lower_price, lp_upper_price = self._domain.strategy_bounds_to_pool(lower_price, upper_price)

        side = self._get_side_from_amounts(lp_base_amt, lp_quote_amt)
        executor_config = LPPositionExecutorConfig(
            timestamp=now,
            connector_name=self._config.connector_name,
            pool_address=self._config.pool_address,
            trading_pair=self._domain.pool_trading_pair,
            base_token=self._domain.pool_base_token,
            quote_token=self._domain.pool_quote_token,
            lower_price=lp_lower_price,
            upper_price=lp_upper_price,
            base_amount=lp_base_amt,
            quote_amount=lp_quote_amt,
            side=side,
            keep_position=False,
            budget_key=self._budget_key,
        )
        extra_params = self._extra_lp_params()
        if extra_params:
            executor_config.extra_params = extra_params
        reservation_id = self._reserve_budget(proposal.open_base, proposal.open_quote)
        if reservation_id is None:
            return None
        executor_config.budget_reservation_id = reservation_id
        return executor_config

    @staticmethod
    def _get_side_from_amounts(base_amt: Decimal, quote_amt: Decimal) -> int:
        if base_amt > 0 and quote_amt > 0:
            return 0
        if quote_amt > 0:
            return 1
        return 2

    def _reserve_budget(self, base_amt: Decimal, quote_amt: Decimal) -> Optional[str]:
        connector_name = self._config.router_connector or self._config.connector_name
        connector = self._market_data_provider.connectors.get(connector_name)
        if connector is None:
            return None
        requirements: Dict[str, Decimal] = {}
        if base_amt > 0:
            requirements[self._domain.base_token] = base_amt
        if quote_amt > 0:
            requirements[self._domain.quote_token] = quote_amt
        reservation_id = self._budget_coordinator.reserve(
            connector_name=connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self._config.native_token_symbol,
            min_native_balance=self._config.min_native_balance,
        )
        return reservation_id
