import asyncio
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

from .components import (
    BalanceSyncBarrier,
    ControllerContext,
    DecisionPatch,
    LPView,
    PoolDomainAdapter,
    Snapshot,
    SwapPurpose,
    SwapView,
)

if TYPE_CHECKING:
    from ..clmm_lp_base import CLMMLPBaseConfig
    from .open_planner import OpenProposal


class SnapshotBuilder:
    def __init__(
        self,
        *,
        controller_id: str,
        config: "CLMMLPBaseConfig",
        domain: PoolDomainAdapter,
        market_data_provider,
    ) -> None:
        self._controller_id = controller_id
        self._config = config
        self._domain = domain
        self._market_data_provider = market_data_provider

    def build(
        self,
        *,
        now: float,
        executors_info: List[ExecutorInfo],
        wallet_base: Decimal,
        wallet_quote: Decimal,
    ) -> Snapshot:
        current_price = self._get_current_price()

        lp: Dict[str, LPView] = {}
        swaps: Dict[str, SwapView] = {}
        for executor in executors_info:
            if executor.controller_id != self._controller_id:
                continue
            if executor.type == "lp_position_executor":
                lp[executor.id] = self._parse_lp_view(executor)
            elif executor.type == "gateway_swap_executor":
                custom = executor.custom_info or {}
                executed_amount_base = self._to_decimal(custom.get("executed_amount_base"))
                executed_amount_quote = self._to_decimal(custom.get("executed_amount_quote"))
                amount_in = self._to_decimal(custom.get("amount_in"))
                amount_out = self._to_decimal(custom.get("amount_out"))
                amount_in_is_quote = custom.get("amount_in_is_quote")
                delta_base = self._to_decimal(custom.get("delta_base"))
                delta_quote = self._to_decimal(custom.get("delta_quote"))
                level_id = getattr(executor.config, "level_id", None)
                swaps[executor.id] = SwapView(
                    executor_id=executor.id,
                    is_active=executor.is_active,
                    is_done=executor.is_done,
                    close_type=executor.close_type,
                    level_id=level_id,
                    purpose=self._swap_purpose(level_id),
                    amount=Decimal(str(getattr(executor.config, "amount", 0))),
                    executed_amount_base=executed_amount_base,
                    executed_amount_quote=executed_amount_quote,
                    amount_in=amount_in,
                    amount_out=amount_out,
                    amount_in_is_quote=amount_in_is_quote,
                    delta_base=delta_base,
                    delta_quote=delta_quote,
                )

        active_lp = [v for v in lp.values() if v.is_active]
        active_swaps = [v for v in swaps.values() if v.is_active]
        return Snapshot(
            now=now,
            current_price=current_price,
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
        price = custom.get("current_price")
        lower = Decimal(str(lower)) if lower is not None else None
        upper = Decimal(str(upper)) if upper is not None else None
        price = Decimal(str(price)) if price is not None else None
        if lower is not None and upper is not None:
            lower, upper = self._domain.pool_bounds_to_strategy(lower, upper, inverted)
        if price is not None:
            price = self._domain.pool_price_to_strategy(price, inverted)
        out_of_range_since = custom.get("out_of_range_since")
        out_of_range_since = float(out_of_range_since) if out_of_range_since is not None else None
        event_seq = custom.get("balance_event_seq")
        event_seq = int(event_seq) if event_seq is not None else 0
        event_type = custom.get("balance_event_type")
        event_base_delta = self._to_decimal(custom.get("balance_event_base_delta"))
        event_quote_delta = self._to_decimal(custom.get("balance_event_quote_delta"))
        if event_base_delta is not None and event_quote_delta is not None:
            event_base_delta, event_quote_delta = self._domain.pool_amounts_to_strategy(
                event_base_delta,
                event_quote_delta,
                inverted,
            )

        return LPView(
            executor_id=executor.id,
            is_active=executor.is_active,
            is_done=executor.is_done,
            close_type=executor.close_type,
            state=custom.get("state"),
            position_address=custom.get("position_address"),
            side=custom.get("side"),
            base_amount=base_amount,
            quote_amount=quote_amount,
            base_fee=base_fee,
            quote_fee=quote_fee,
            lower_price=lower,
            upper_price=upper,
            current_price=price,
            out_of_range_since=out_of_range_since,
            balance_event_seq=event_seq,
            balance_event_type=event_type,
            balance_event_base_delta=event_base_delta,
            balance_event_quote_delta=event_quote_delta,
        )

    def _get_current_price(self) -> Optional[Decimal]:
        price = self._market_data_provider.get_rate(self._config.trading_pair)
        if price is None:
            return None
        return Decimal(str(price))

    @staticmethod
    def _to_decimal(value: Optional[object]) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _swap_purpose(level_id: Optional[str]) -> Optional[SwapPurpose]:
        if level_id == SwapPurpose.INVENTORY.value:
            return SwapPurpose.INVENTORY
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
        ctx: ControllerContext,
        logger: Callable[[], HummingbotLogger],
    ) -> None:
        self._config = config
        self._domain = domain
        self._market_data_provider = market_data_provider
        self._ctx = ctx
        self._logger = logger

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")
        self._last_balance_update_ts: float = 0.0
        self._last_balance_attempt_ts: float = 0.0
        self._wallet_update_task: Optional[asyncio.Task] = None
        self._has_balance_snapshot: bool = False
        self._logged_balance_snapshot: bool = False
        self._unassigned_delta_base: Decimal = Decimal("0")
        self._unassigned_delta_quote: Decimal = Decimal("0")

    @property
    def wallet_base(self) -> Decimal:
        return self._wallet_base

    @property
    def wallet_quote(self) -> Decimal:
        return self._wallet_quote

    @property
    def has_snapshot(self) -> bool:
        return self._has_balance_snapshot

    def schedule_refresh(self, now: float) -> None:
        if self._wallet_update_task is not None and not self._wallet_update_task.done():
            return
        barrier = self._ctx.swap.balance_barrier
        if barrier is not None:
            min_interval = self._balance_refresh_backoff(barrier.attempts)
            if (now - barrier.last_attempt_ts) < min_interval:
                return
        if (now - self._last_balance_attempt_ts) < 1.0:
            return
        if self._config.balance_refresh_interval_sec > 0:
            if barrier is None and not self._ctx.swap.awaiting_balance_refresh and (
                (now - self._last_balance_update_ts) < self._config.balance_refresh_interval_sec
            ):
                return

        connector = self._market_data_provider.connectors.get(self._config.connector_name)
        if connector is None:
            return
        self._last_balance_attempt_ts = now
        if barrier is not None:
            barrier.last_attempt_ts = now
            barrier.attempts += 1
        self._wallet_update_task = safe_ensure_future(self._update_wallet_balances(connector))
        self._wallet_update_task.add_done_callback(self._clear_wallet_update_task)

    def clear_stale_refresh(self, now: float) -> None:
        barrier = self._ctx.swap.balance_barrier
        if barrier is None:
            return
        if barrier.deadline_ts <= 0:
            barrier.deadline_ts = now + self._config.balance_refresh_timeout_sec
            return
        if now < barrier.deadline_ts:
            return
        if not self._ctx.failure.blocked:
            self._logger().error(
                "balance_sync_timeout | reason=%s age=%.0f attempts=%s",
                barrier.reason or "unknown",
                now - barrier.created_ts,
                barrier.attempts,
            )
            patch = DecisionPatch()
            patch.failure.set_reason = "balance_sync_timeout"
            self._ctx.apply(patch)

    def request_balance_sync(
        self,
        *,
        now: float,
        delta_base: Decimal,
        delta_quote: Decimal,
        reason: str,
    ) -> None:
        if delta_base == 0 and delta_quote == 0:
            return
        barrier = self._ctx.swap.balance_barrier
        if barrier is None and self._consume_unassigned_delta(delta_base, delta_quote):
            return
        if barrier is None:
            barrier = BalanceSyncBarrier(
                baseline_base=self._wallet_base,
                baseline_quote=self._wallet_quote,
                created_ts=now,
                deadline_ts=now + self._config.balance_refresh_timeout_sec,
                reason=reason,
            )
            self._ctx.swap.balance_barrier = barrier
            self._logger().info(
                "balance_sync_start | reason=%s baseline_base=%s baseline_quote=%s",
                reason,
                self._wallet_base,
                self._wallet_quote,
            )
        barrier.expected_delta_base += delta_base
        barrier.expected_delta_quote += delta_quote
        barrier.deadline_ts = max(barrier.deadline_ts, now + self._config.balance_refresh_timeout_sec)
        self._ctx.swap.awaiting_balance_refresh = True
        if self._ctx.swap.awaiting_balance_refresh_since <= 0:
            self._ctx.swap.awaiting_balance_refresh_since = now

    def _clear_wallet_update_task(self, task: asyncio.Task) -> None:
        if self._wallet_update_task is task:
            self._wallet_update_task = None

    async def _update_wallet_balances(self, connector) -> None:
        try:
            timeout = float(max(1, self._config.balance_update_timeout_sec))
            await asyncio.wait_for(connector.update_balances(), timeout=timeout)
            prev_base = self._wallet_base
            prev_quote = self._wallet_quote
            self._wallet_base = Decimal(str(connector.get_available_balance(self._domain.base_token) or 0))
            self._wallet_quote = Decimal(str(connector.get_available_balance(self._domain.quote_token) or 0))
            self._last_balance_update_ts = self._market_data_provider.time()
            barrier = self._ctx.swap.balance_barrier
            if self._has_balance_snapshot and barrier is None:
                self._unassigned_delta_base += self._wallet_base - prev_base
                self._unassigned_delta_quote += self._wallet_quote - prev_quote
            self._has_balance_snapshot = True
            if not self._logged_balance_snapshot:
                self._logger().info(
                    "balance_snapshot_ready | base=%s quote=%s",
                    self._wallet_base,
                    self._wallet_quote,
                )
                self._logged_balance_snapshot = True
            if barrier is None:
                self._ctx.swap.awaiting_balance_refresh = False
                self._ctx.swap.awaiting_balance_refresh_since = 0.0
                return
            if self._is_balance_synced(barrier):
                self._logger().info(
                    "balance_sync_done | reason=%s observed_base=%s observed_quote=%s",
                    barrier.reason or "unknown",
                    self._wallet_base - barrier.baseline_base,
                    self._wallet_quote - barrier.baseline_quote,
                )
                self._ctx.swap.balance_barrier = None
                self._ctx.swap.awaiting_balance_refresh = False
                self._ctx.swap.awaiting_balance_refresh_since = 0.0
        except Exception:
            self._logger().exception(
                "update_balances failed | connector=%s base=%s quote=%s last_update_ts=%.0f last_attempt_ts=%.0f",
                self._config.connector_name,
                self._domain.base_token,
                self._domain.quote_token,
                self._last_balance_update_ts,
                self._last_balance_attempt_ts,
            )

    @staticmethod
    def _balance_refresh_backoff(attempts: int) -> float:
        if attempts <= 0:
            return 3.0
        return min(20.0, 3.0 * (2 ** min(attempts, 3)))

    def _is_balance_synced(self, barrier: BalanceSyncBarrier) -> bool:
        observed_base = self._wallet_base - barrier.baseline_base
        observed_quote = self._wallet_quote - barrier.baseline_quote
        expected_base = barrier.expected_delta_base
        expected_quote = barrier.expected_delta_quote
        tol_base = self._sync_tolerance(expected_base)
        tol_quote = self._sync_tolerance(expected_quote)
        return (
            abs(observed_base - expected_base) <= tol_base
            and abs(observed_quote - expected_quote) <= tol_quote
        )

    @staticmethod
    def _sync_tolerance(expected: Decimal) -> Decimal:
        rel_tol = abs(expected) * Decimal("0.001")
        abs_tol = Decimal("0.00000001")
        return max(rel_tol, abs_tol)

    @staticmethod
    def _delta_sign_matches(observed: Decimal, expected: Decimal) -> bool:
        if expected == 0:
            return True
        if observed == 0:
            return False
        return (observed > 0 and expected > 0) or (observed < 0 and expected < 0)

    def _consume_unassigned_delta(self, delta_base: Decimal, delta_quote: Decimal) -> bool:
        if not self._has_balance_snapshot:
            return False
        if not self._delta_sign_matches(self._unassigned_delta_base, delta_base):
            return False
        if not self._delta_sign_matches(self._unassigned_delta_quote, delta_quote):
            return False
        if abs(delta_base) > abs(self._unassigned_delta_base) + self._sync_tolerance(delta_base):
            return False
        if abs(delta_quote) > abs(self._unassigned_delta_quote) + self._sync_tolerance(delta_quote):
            return False
        self._unassigned_delta_base -= delta_base
        self._unassigned_delta_quote -= delta_quote
        return True


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
        connector = self._market_data_provider.connectors.get(self._config.connector_name)
        if connector is None:
            return None
        requirements: Dict[str, Decimal] = {}
        if base_amt > 0:
            requirements[self._domain.base_token] = base_amt
        if quote_amt > 0:
            requirements[self._domain.quote_token] = quote_amt
        reservation_id = self._budget_coordinator.reserve(
            connector_name=self._config.connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self._config.native_token_symbol,
            min_native_balance=self._config.min_native_balance,
        )
        return reservation_id
