import logging
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from pydantic import Field, field_validator, model_validator

from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.budget.budget_coordinator import BudgetCoordinatorRegistry
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig, LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

from .clmm_lp_components import (
    COST_FILTER_FEE_EWMA_ALPHA,
    COST_FILTER_FEE_SAMPLE_MIN_SECONDS,
    BudgetAnchor,
    ControllerContext,
    ControllerState,
    Decision,
    DecisionPatch,
    EntryLog,
    Intent,
    IntentFlow,
    IntentStage,
    LPView,
    LpContext,
    PositionBudget,
    RebalancePlan,
    Snapshot,
    SwapView,
    TokenOrderMapper,
    evaluate_cost_filter,
    should_force_rebalance,
    to_decimal,
    to_optional_decimal,
)


class StopLossLiquidationMode(str, Enum):
    NONE = "none"
    QUOTE = "quote"


class CLMMLPGuardedControllerConfig(ControllerConfigBase):
    controller_type: str = "generic"
    controller_name: str = "clmm_lp"
    candles_config: List[CandlesConfig] = []

    connector_name: str = "meteora/clmm"
    router_connector: str = "jupiter/router"
    trading_pair: str = "SOL-USDC"
    pool_trading_pair: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    pool_address: str = ""

    target_price: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    trigger_above: bool = Field(default=True, json_schema_extra={"is_updatable": True})

    position_value_quote: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})

    position_width_pct: Decimal = Field(default=Decimal("12"), json_schema_extra={"is_updatable": True})
    rebalance_seconds: int = Field(default=60, json_schema_extra={"is_updatable": True})
    hysteresis_pct: Decimal = Field(default=Decimal("0.20"), json_schema_extra={"is_updatable": True})
    cooldown_seconds: int = Field(default=30, json_schema_extra={"is_updatable": True})
    max_rebalances_per_hour: int = Field(default=20, json_schema_extra={"is_updatable": True})
    reopen_delay_sec: int = Field(default=5, json_schema_extra={"is_updatable": True})

    auto_swap_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})
    target_base_value_pct: Decimal = Field(default=Decimal("0.5"), json_schema_extra={"is_updatable": True})
    swap_min_value_pct: Decimal = Field(default=Decimal("0.05"), json_schema_extra={"is_updatable": True})
    swap_safety_buffer_pct: Decimal = Field(default=Decimal("2"), json_schema_extra={"is_updatable": True})
    swap_timeout_sec: int = Field(default=120, json_schema_extra={"is_updatable": True})
    swap_poll_interval_sec: Decimal = Field(default=Decimal("2"), json_schema_extra={"is_updatable": True})
    swap_slippage_pct: Decimal = Field(default=Decimal("1"), json_schema_extra={"is_updatable": True})
    swap_retry_attempts: int = Field(default=0, json_schema_extra={"is_updatable": True})
    swap_retry_delay_sec: Decimal = Field(default=Decimal("1"), json_schema_extra={"is_updatable": True})

    cost_filter_enabled: bool = Field(default=False, json_schema_extra={"is_updatable": True})
    cost_filter_fee_rate_bootstrap_quote_per_hour: Decimal = Field(
        default=Decimal("0"),
        json_schema_extra={"is_updatable": True},
    )
    cost_filter_fixed_cost_quote: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    cost_filter_max_payback_sec: int = Field(default=3600, json_schema_extra={"is_updatable": True})

    stop_loss_pnl_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    stop_loss_pause_sec: int = Field(default=1800, json_schema_extra={"is_updatable": True})
    stop_loss_liquidation_mode: StopLossLiquidationMode = Field(
        default=StopLossLiquidationMode.QUOTE,
        json_schema_extra={"is_updatable": True},
    )
    reenter_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})

    budget_key: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    native_token_symbol: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    min_native_balance: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    balance_refresh_interval_sec: int = Field(default=10, json_schema_extra={"is_updatable": True})

    @field_validator("position_value_quote", mode="before")
    @classmethod
    def validate_position_value_quote(cls, v):
        value = Decimal(str(v))
        if value <= 0:
            raise ValueError("position_value_quote must be > 0")
        return value

    @field_validator("target_base_value_pct", mode="before")
    @classmethod
    def validate_target_base_value_pct(cls, v):
        value = Decimal(str(v))
        if value < 0 or value > 1:
            raise ValueError("target_base_value_pct must be between 0 and 1")
        return value

    @field_validator("swap_min_value_pct", mode="before")
    @classmethod
    def validate_swap_min_value_pct(cls, v):
        value = Decimal(str(v))
        if value < 0 or value > 1:
            raise ValueError("swap_min_value_pct must be between 0 and 1")
        return value

    @field_validator("pool_trading_pair", mode="before")
    @classmethod
    def normalize_pool_trading_pair(cls, v):
        if v is None:
            return None
        value = str(v).strip()
        return value or None

    @model_validator(mode="after")
    def validate_pool_trading_pair(self):
        if not self.pool_trading_pair:
            return self
        ref_tokens = self.trading_pair.split("-")
        pool_tokens = self.pool_trading_pair.split("-")
        if len(ref_tokens) != 2:
            raise ValueError("trading_pair must be in BASE-QUOTE format")
        if len(pool_tokens) != 2:
            raise ValueError("pool_trading_pair must be in BASE-QUOTE format")
        if pool_tokens != ref_tokens and pool_tokens != list(reversed(ref_tokens)):
            raise ValueError("pool_trading_pair must match trading_pair or be its reverse")
        return self

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pool_pair = self.pool_trading_pair or self.trading_pair
        markets = markets.add_or_update(self.connector_name, pool_pair)
        markets = markets.add_or_update(self.router_connector, self.trading_pair)
        return markets


class CLMMLPGuardedController(ControllerBase):
    _logger: Optional[HummingbotLogger] = None

    @staticmethod
    def _addresses_equal(a: Optional[str], b: Optional[str]) -> bool:
        if not a or not b:
            return False
        if a.startswith("0x") and b.startswith("0x"):
            return a.lower() == b.lower()
        return a == b

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: CLMMLPGuardedControllerConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: CLMMLPGuardedControllerConfig = config

        self._tokens = TokenOrderMapper.from_config(config.trading_pair, config.pool_trading_pair)

        self._budget_key = self.config.budget_key or self.config.id
        self._budget_coordinator = BudgetCoordinatorRegistry.get(self._budget_key)
        self._stop_loss_liquidation_mode = self.config.stop_loss_liquidation_mode

        self._ctx = ControllerContext()
        self._pending_actions: List[ExecutorAction] = []

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")
        self._last_balance_update_ts: float = 0.0

        self._pool_price: Optional[Decimal] = None
        self._pool_price_ts: float = 0.0

        self._one_sided_open_warned: Set[str] = set()
        self._last_balance_log_ts: float = 0.0
        self._balance_log_interval: float = 30.0
        self._last_tick_log_ts: float = 0.0
        self._tick_log_interval: float = 30.0
        self._last_entry_log_ts: float = 0.0
        self._entry_log_interval: float = 30.0
        self._last_balance_error_log_ts: float = 0.0
        self._balance_error_log_interval: float = 30.0
        self._last_pool_price_error_log_ts: float = 0.0
        self._pool_price_error_log_interval: float = 30.0
        self._last_cost_filter_log_ts: float = 0.0

        rate_connector = self.config.router_connector
        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=rate_connector,
                trading_pair=self.config.trading_pair,
            ),
        ])
        if self._tokens.pool_trading_pair != self._tokens.trading_pair:
            self.logger().info(
                "Token order mapping enabled: ref_pair=%s pool_pair=%s inverted=%s",
                self._tokens.trading_pair,
                self._tokens.pool_trading_pair,
                self._tokens.pool_order_inverted,
            )

    async def update_processed_data(self):
        now = self.market_data_provider.time()

        self._reconcile_done_swaps(now)
        force_refresh = self._ctx.swap.awaiting_balance_refresh

        await self._update_wallet_balances(now)
        await self._update_pool_price(now, force=force_refresh)

        snapshot = self._build_snapshot(now)

        self._ensure_anchors(snapshot)
        self._update_fee_rate_estimates(snapshot)
        self._maybe_warn_one_sided_opens(snapshot)

        decision = self._decide(snapshot)
        self._apply_patch(decision.patch)
        self._pending_actions = decision.actions

        derived_state, reason = self._derive_view_state(snapshot, decision.intent)
        if decision.entry_log is not None:
            self._maybe_log_entry(
                now=snapshot.now,
                reason=decision.entry_log.reason,
                current_price=decision.entry_log.current_price,
                details=decision.entry_log.details,
            )
        self._maybe_log_tick(now, snapshot.current_price, derived_state, reason)

        self.processed_data = {
            "current_price": snapshot.current_price,
            "router_price": snapshot.router_price,
            "pool_price": snapshot.pool_price,
            "wallet_base": snapshot.wallet_base,
            "wallet_quote": snapshot.wallet_quote,
            "controller_state": derived_state.value,
            "state_reason": reason,
            "intent_flow": decision.intent.flow.value,
            "intent_stage": decision.intent.stage.value,
            "intent_reason": decision.intent.reason,
            "pending_liquidation": self._ctx.stoploss.pending_liquidation,
            "inventory_swap_failed": self._ctx.swap.inventory_swap_failed,
            "stop_loss_active": snapshot.now < self._ctx.stoploss.until_ts,
            "stop_loss_until_ts": self._ctx.stoploss.until_ts if self._ctx.stoploss.until_ts > 0 else None,
            "rebalance_pending": len(self._ctx.rebalance.plans),
            "lp_failure_blocked": self._ctx.failure.blocked,
            "active_lp": [
                {
                    "id": v.executor_id,
                    "state": v.state,
                    "position": v.position_address,
                    "base": str(v.base_amount),
                    "quote": str(v.quote_amount),
                    "lower": str(v.lower_price) if v.lower_price is not None else None,
                    "upper": str(v.upper_price) if v.upper_price is not None else None,
                }
                for v in snapshot.active_lp
            ],
            "active_swaps": [
                {"id": v.executor_id, "level_id": v.level_id, "close_type": v.close_type.value if v.close_type else None}
                for v in snapshot.active_swaps
            ],
        }

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions = self._pending_actions
        self._pending_actions = []
        return actions

    def _build_snapshot(self, now: float) -> Snapshot:
        router_price = self._get_router_price()
        current_price = self._get_current_price(now, router_price)

        lp: Dict[str, LPView] = {}
        swaps: Dict[str, SwapView] = {}
        for executor in self.executors_info:
            if executor.controller_id != self.config.id:
                continue
            if executor.type == "lp_position_executor":
                lp[executor.id] = self._parse_lp_view(executor)
            elif executor.type == "gateway_swap_executor":
                swaps[executor.id] = SwapView(
                    executor_id=executor.id,
                    is_active=executor.is_active,
                    is_done=executor.is_done,
                    close_type=executor.close_type,
                    level_id=getattr(executor.config, "level_id", None),
                )

        return Snapshot(
            now=now,
            current_price=current_price,
            pool_price=self._pool_price,
            router_price=router_price,
            wallet_base=self._wallet_base,
            wallet_quote=self._wallet_quote,
            lp=lp,
            swaps=swaps,
        )

    def _parse_lp_view(self, executor: ExecutorInfo) -> LPView:
        custom = executor.custom_info or {}
        lp_base_amount = to_decimal(custom.get("base_amount", 0))
        lp_quote_amount = to_decimal(custom.get("quote_amount", 0))
        lp_base_fee = to_decimal(custom.get("base_fee", 0))
        lp_quote_fee = to_decimal(custom.get("quote_fee", 0))

        inverted = self._tokens.executor_token_order_inverted(executor)
        if inverted is None:
            inverted = self._tokens.pool_order_inverted
        base_amount, quote_amount = self._tokens.lp_amounts_to_strategy(lp_base_amount, lp_quote_amount, inverted)
        base_fee, quote_fee = self._tokens.lp_amounts_to_strategy(lp_base_fee, lp_quote_fee, inverted)

        lower = to_optional_decimal(custom.get("lower_price"))
        upper = to_optional_decimal(custom.get("upper_price"))
        price = to_optional_decimal(custom.get("current_price"))
        if lower is not None and upper is not None:
            lower, upper = self._tokens.lp_bounds_to_strategy(lower, upper, inverted)
        if price is not None:
            price = self._tokens.lp_price_to_strategy(price, inverted)

        return LPView(
            executor_id=executor.id,
            is_active=executor.is_active,
            is_done=executor.is_done,
            close_type=executor.close_type,
            state=(custom.get("state") if isinstance(custom.get("state"), str) else None),
            position_address=(custom.get("position_address") if isinstance(custom.get("position_address"), str) else None),
            side=(custom.get("side") if isinstance(custom.get("side"), str) else None),
            base_amount=base_amount,
            quote_amount=quote_amount,
            base_fee=base_fee,
            quote_fee=quote_fee,
            lower_price=lower,
            upper_price=upper,
            current_price=price,
            out_of_range_since=custom.get("out_of_range_since"),
        )

    def _get_router_price(self) -> Optional[Decimal]:
        price = self.market_data_provider.get_rate(self.config.trading_pair)
        if price is None:
            return None
        try:
            return Decimal(str(price))
        except Exception:
            return None

    def _get_current_price(self, now: float, router_price: Optional[Decimal]) -> Optional[Decimal]:
        pool_price = self._pool_price
        if pool_price is not None and pool_price > 0:
            max_age = max(30.0, float(max(0, self.config.balance_refresh_interval_sec)) * 3.0)
            if (now - self._pool_price_ts) <= max_age:
                return pool_price
        return router_price

    async def _update_pool_price(self, now: float, *, force: bool = False):
        pool_address = self.config.pool_address
        if not pool_address:
            return
        if not force and self.config.balance_refresh_interval_sec > 0:
            if (now - self._pool_price_ts) < self.config.balance_refresh_interval_sec:
                return

        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is None:
            return

        pool_info = None
        try:
            if hasattr(connector, "get_pool_info_by_address"):
                pool_info = await connector.get_pool_info_by_address(pool_address)
            elif hasattr(connector, "get_pool_info"):
                pool_info = await connector.get_pool_info(self._tokens.pool_trading_pair)
        except Exception as exc:
            self._maybe_log_pool_price_error(now, exc)
            return

        if pool_info is None:
            return

        price = getattr(pool_info, "price", None)
        if price is None:
            return
        try:
            pool_price = Decimal(str(price))
        except Exception:
            return
        if pool_price <= 0:
            return

        base_addr = getattr(pool_info, "base_token_address", None)
        quote_addr = getattr(pool_info, "quote_token_address", None)
        if base_addr is None or quote_addr is None:
            return

        inverted: Optional[bool] = None
        if hasattr(connector, "get_token_info"):
            base_info = connector.get_token_info(self._tokens.base_token)
            quote_info = connector.get_token_info(self._tokens.quote_token)
            base_token_addr = base_info.get("address") if base_info else None
            quote_token_addr = quote_info.get("address") if quote_info else None
            if base_token_addr and quote_token_addr:
                if self._addresses_equal(base_token_addr, base_addr) and self._addresses_equal(quote_token_addr, quote_addr):
                    inverted = False
                elif self._addresses_equal(base_token_addr, quote_addr) and self._addresses_equal(quote_token_addr, base_addr):
                    inverted = True

        if inverted is None and hasattr(connector, "get_token_by_address"):
            base_from_addr = connector.get_token_by_address(base_addr)
            quote_from_addr = connector.get_token_by_address(quote_addr)
            base_sym = base_from_addr.get("symbol") if base_from_addr else None
            quote_sym = quote_from_addr.get("symbol") if quote_from_addr else None
            if base_sym and quote_sym:
                if base_sym == self._tokens.base_token and quote_sym == self._tokens.quote_token:
                    inverted = False
                elif base_sym == self._tokens.quote_token and quote_sym == self._tokens.base_token:
                    inverted = True

        if inverted is True:
            pool_price = Decimal("1") / pool_price
        elif inverted is None:
            return

        self._pool_price = pool_price
        self._pool_price_ts = now

    def _maybe_log_pool_price_error(self, now: float, exc: Exception):
        if (now - self._last_pool_price_error_log_ts) < self._pool_price_error_log_interval:
            return
        self._last_pool_price_error_log_ts = now
        self.logger().warning(
            "Pool price update failed (%s): %s",
            self.config.connector_name,
            exc,
        )

    async def _update_wallet_balances(self, now: float):
        if self.config.balance_refresh_interval_sec > 0:
            if not self._ctx.swap.awaiting_balance_refresh and (
                (now - self._last_balance_update_ts) < self.config.balance_refresh_interval_sec
            ):
                return

        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is None:
            return
        try:
            await connector.update_balances()
            self._wallet_base = Decimal(str(connector.get_balance(self._tokens.base_token) or 0))
            self._wallet_quote = Decimal(str(connector.get_balance(self._tokens.quote_token) or 0))
            self._last_balance_update_ts = now
            self._ctx.swap.awaiting_balance_refresh = False
            self._maybe_log_balances(now)
        except Exception as exc:
            self._maybe_log_balance_error(now, exc)
            return

    def _maybe_log_balances(self, now: float):
        if (now - self._last_balance_log_ts) < self._balance_log_interval:
            return
        self._last_balance_log_ts = now
        self.logger().info(
            "Wallet balances (%s): base=%s quote=%s",
            self.config.connector_name,
            self._wallet_base,
            self._wallet_quote,
        )

    def _maybe_log_balance_error(self, now: float, exc: Exception):
        if (now - self._last_balance_error_log_ts) < self._balance_error_log_interval:
            return
        self._last_balance_error_log_ts = now
        self.logger().warning(
            "Balance update failed (%s): %s",
            self.config.connector_name,
            exc,
        )

    def _maybe_log_entry(
        self,
        *,
        now: float,
        reason: str,
        current_price: Optional[Decimal] = None,
        details: Optional[Dict[str, object]] = None,
    ):
        if (now - self._last_entry_log_ts) < self._entry_log_interval:
            return
        self._last_entry_log_ts = now
        suffix = ""
        if details:
            suffix = f" details={details}"
        self.logger().info(
            "Entry check: reason=%s wallet_base=%s wallet_quote=%s price=%s%s",
            reason,
            self._wallet_base,
            self._wallet_quote,
            current_price,
            suffix,
        )

    def _maybe_log_tick(self, now: float, current_price: Optional[Decimal], state: ControllerState, reason: Optional[str]):
        if (now - self._last_tick_log_ts) < self._tick_log_interval:
            return
        self._last_tick_log_ts = now
        self.logger().info(
            "Controller tick: state=%s price=%s swap_failed=%s pending_liquidation=%s reason=%s",
            state.value,
            current_price,
            self._ctx.swap.inventory_swap_failed,
            self._ctx.stoploss.pending_liquidation,
            reason,
        )

    def _ensure_anchors(self, snapshot: Snapshot):
        price = snapshot.current_price
        if price is None or price <= 0:
            return
        for lp_view in snapshot.active_lp:
            lp_ctx = self._ctx.lp.setdefault(lp_view.executor_id, LpContext())
            anchor = lp_ctx.anchor
            if anchor is not None and anchor.value_quote > 0:
                continue
            new_anchor = self._build_budget_anchor(price, lp_view, snapshot.wallet_base, snapshot.wallet_quote)
            if new_anchor is None:
                continue
            lp_ctx.anchor = new_anchor
            self.logger().info(
                "Anchor budget initialized: executor=%s value=%.6f",
                lp_view.executor_id,
                float(new_anchor.value_quote),
            )

    def _build_budget_anchor(
        self,
        current_price: Decimal,
        lp_view: LPView,
        wallet_base: Decimal,
        wallet_quote: Decimal,
    ) -> Optional[BudgetAnchor]:
        budget_value = max(Decimal("0"), self.config.position_value_quote)
        if budget_value <= 0:
            return None
        deployed_value = self._estimate_position_value(lp_view, current_price)
        wallet_value = wallet_base * current_price + wallet_quote
        remaining_value = budget_value - deployed_value
        if remaining_value <= 0 or wallet_value <= 0:
            anchor_value = max(Decimal("0"), deployed_value)
            if anchor_value <= 0:
                return None
            return BudgetAnchor(
                value_quote=anchor_value,
                wallet_base_amount=Decimal("0"),
                wallet_quote_amount=Decimal("0"),
            )

        budget_wallet_value = min(wallet_value, remaining_value)
        base_value = wallet_base * current_price
        base_ratio = base_value / wallet_value if wallet_value > 0 else Decimal("0")
        base_slice_value = budget_wallet_value * base_ratio
        wallet_base_amount = base_slice_value / current_price
        wallet_quote_amount = budget_wallet_value - base_slice_value
        anchor_value = deployed_value + budget_wallet_value
        if anchor_value <= 0:
            return None
        return BudgetAnchor(
            value_quote=anchor_value,
            wallet_base_amount=wallet_base_amount,
            wallet_quote_amount=wallet_quote_amount,
        )

    def _estimate_position_value(self, lp_view: LPView, current_price: Decimal) -> Decimal:
        return (lp_view.base_amount + lp_view.base_fee) * current_price + (lp_view.quote_amount + lp_view.quote_fee)

    def _update_fee_rate_estimates(self, snapshot: Snapshot):
        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return
        for lp_view in snapshot.active_lp:
            self._update_fee_rate_estimate_for_executor(snapshot.now, current_price, lp_view)

    def _update_fee_rate_estimate_for_executor(self, now: float, current_price: Decimal, lp_view: LPView):
        if lp_view.state != LPPositionStates.IN_RANGE.value:
            return
        position_address = lp_view.position_address
        if not position_address:
            return

        ctx = self._ctx.lp.setdefault(lp_view.executor_id, LpContext()).fee
        if ctx.last_position_address != position_address:
            ctx.last_position_address = position_address
            ctx.last_fee_value = None
            ctx.last_fee_ts = None
            ctx.fee_rate_ewma = None
            return

        pending_fee = lp_view.base_fee * current_price + lp_view.quote_fee
        if ctx.last_fee_ts is None or ctx.last_fee_value is None:
            ctx.last_fee_ts = now
            ctx.last_fee_value = pending_fee
            return

        dt = Decimal(str(now - ctx.last_fee_ts))
        if dt <= 0:
            return
        if dt < COST_FILTER_FEE_SAMPLE_MIN_SECONDS:
            return

        delta = pending_fee - ctx.last_fee_value
        if delta < 0:
            ctx.last_fee_ts = now
            ctx.last_fee_value = pending_fee
            return

        fee_rate = delta / dt
        alpha = COST_FILTER_FEE_EWMA_ALPHA
        if ctx.fee_rate_ewma is None:
            ctx.fee_rate_ewma = fee_rate
        else:
            ctx.fee_rate_ewma = (ctx.fee_rate_ewma * (Decimal("1") - alpha)) + (fee_rate * alpha)

        ctx.last_fee_ts = now
        ctx.last_fee_value = pending_fee

    def _maybe_warn_one_sided_opens(self, snapshot: Snapshot):
        current_price = snapshot.current_price
        for lp_view in snapshot.active_lp:
            self._maybe_warn_one_sided_open(lp_view, current_price, snapshot)

    def _maybe_warn_one_sided_open(self, lp_view: LPView, current_price: Optional[Decimal], snapshot: Snapshot):
        if lp_view.side != "BOTH":
            return
        if lp_view.state not in {LPPositionStates.IN_RANGE.value, LPPositionStates.OUT_OF_RANGE.value}:
            return
        position_address = lp_view.position_address or ""
        if not position_address:
            return
        if position_address in self._one_sided_open_warned:
            return
        if lp_view.base_amount > 0 and lp_view.quote_amount > 0:
            return

        self._one_sided_open_warned.add(position_address)
        self.logger().warning(
            "LP opened one-sided (side=BOTH): position=%s state=%s base=%s quote=%s "
            "lower=%s upper=%s exec_price=%s controller_price=%s pool_price=%s router_price=%s",
            position_address,
            lp_view.state,
            lp_view.base_amount,
            lp_view.quote_amount,
            lp_view.lower_price,
            lp_view.upper_price,
            lp_view.current_price,
            current_price,
            snapshot.pool_price,
            snapshot.router_price,
        )

    def _decide(self, snapshot: Snapshot) -> Decision:
        now = snapshot.now

        if self.config.manual_kill_switch:
            actions = [StopExecutorAction(controller_id=self.config.id, executor_id=v.executor_id) for v in snapshot.active_lp]
            return Decision(
                intent=Intent(flow=IntentFlow.MANUAL, stage=IntentStage.STOP_LP, reason="manual_kill_switch"),
                actions=actions,
            )

        if self._ctx.failure.blocked:
            return Decision(
                intent=Intent(flow=IntentFlow.FAILURE, stage=IntentStage.WAIT, reason=self._ctx.failure.reason or "lp_failure"),
            )

        detected = self._detect_lp_failure()
        if detected is not None:
            failed_id, reason = detected
            actions: List[ExecutorAction] = []
            failed_view = snapshot.lp.get(failed_id)
            if failed_view is not None and failed_view.is_active:
                actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=failed_id))
            patch = DecisionPatch(
                set_failure_reason=reason,
                clear_rebalance_all=True,
                set_stoploss_pending_liquidation=False,
            )
            self.logger().error("LP executor failure detected (%s). Manual intervention required.", reason)
            return Decision(
                intent=Intent(flow=IntentFlow.FAILURE, stage=IntentStage.STOP_LP, reason=reason),
                actions=actions,
                patch=patch,
            )

        if snapshot.active_swaps:
            label = snapshot.active_swaps[0].level_id or "swap"
            if label == "liquidate":
                flow = IntentFlow.STOPLOSS
            elif self._ctx.rebalance.plans:
                flow = IntentFlow.REBALANCE
            else:
                flow = IntentFlow.ENTRY
            return Decision(
                intent=Intent(flow=flow, stage=IntentStage.WAIT, reason=f"{label}_in_progress"),
            )

        stoploss_decision = self._decide_stoploss(snapshot)
        if stoploss_decision is not None:
            return stoploss_decision

        rebalance_stop = self._decide_rebalance_stops(snapshot)
        if rebalance_stop is not None:
            return rebalance_stop

        if snapshot.active_lp and not self._ctx.rebalance.plans:
            return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.WAIT, reason="lp_active"))

        if self._ctx.stoploss.pending_liquidation:
            return self._decide_liquidation(snapshot)

        if now < self._ctx.stoploss.until_ts:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="cooldown"))

        if self._ctx.rebalance.plans:
            rebalance_open = self._decide_rebalance_reopen(snapshot)
            if rebalance_open is not None:
                return rebalance_open
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="rebalance_wait"))

        return self._decide_entry(snapshot)

    def _detect_lp_failure(self) -> Optional[Tuple[str, str]]:
        for executor in self.executors_info:
            if executor.type != "lp_position_executor":
                continue
            if executor.controller_id != self.config.id:
                continue
            state = (executor.custom_info or {}).get("state")
            if state == LPPositionStates.RETRIES_EXCEEDED.value:
                return executor.id, "retries_exceeded"
            if executor.close_type == CloseType.FAILED:
                return executor.id, "executor_failed"
        return None

    def _decide_stoploss(self, snapshot: Snapshot) -> Optional[Decision]:
        if self.config.stop_loss_pnl_pct <= 0:
            return None
        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return None

        triggered = False
        for lp_view in snapshot.active_lp:
            lp_ctx = self._ctx.lp.get(lp_view.executor_id)
            anchor = lp_ctx.anchor if lp_ctx is not None else None
            if anchor is None or anchor.value_quote <= 0:
                continue
            equity = self._estimate_position_value(lp_view, current_price) + (
                anchor.wallet_base_amount * current_price + anchor.wallet_quote_amount
            )
            trigger_level = anchor.value_quote - (anchor.value_quote * self.config.stop_loss_pnl_pct)
            if equity <= trigger_level:
                triggered = True
                break

        if not triggered:
            return None

        actions = [StopExecutorAction(controller_id=self.config.id, executor_id=v.executor_id) for v in snapshot.active_lp]
        patch = DecisionPatch(
            clear_rebalance_all=True,
            set_stoploss_last_exit_reason="stop_loss",
            set_stoploss_until_ts=snapshot.now + self.config.stop_loss_pause_sec,
        )
        if self._stop_loss_liquidation_mode == StopLossLiquidationMode.QUOTE:
            patch.set_stoploss_pending_liquidation = True
            patch.set_stoploss_last_liquidation_attempt_ts = 0.0
            patch.set_stoploss_liquidation_target_base = self._compute_liquidation_target_base(snapshot, snapshot.active_lp)
        return Decision(
            intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.STOP_LP, reason="stop_loss_triggered"),
            actions=actions,
            patch=patch,
        )

    def _compute_liquidation_target_base(self, snapshot: Snapshot, stopped: List[LPView]) -> Optional[Decimal]:
        total: Decimal = Decimal("0")
        for lp_view in stopped:
            ctx = self._ctx.lp.get(lp_view.executor_id)
            if ctx is None or ctx.open_base is None:
                continue
            total += max(Decimal("0"), ctx.open_base)
        if total > 0:
            return total
        budget = self._build_position_budget(snapshot.current_price)
        return budget.target_base if budget is not None else None

    def _decide_liquidation(self, snapshot: Snapshot) -> Decision:
        now = snapshot.now
        if self._ctx.stoploss.last_liquidation_attempt_ts > 0 and self.config.cooldown_seconds > 0:
            if (now - self._ctx.stoploss.last_liquidation_attempt_ts) < self.config.cooldown_seconds:
                return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_cooldown"))

        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="price_unavailable"))

        base_to_liquidate = snapshot.wallet_base
        target = self._ctx.stoploss.liquidation_target_base
        if target is not None:
            base_to_liquidate = min(base_to_liquidate, max(Decimal("0"), target))

        if base_to_liquidate <= 0:
            patch = DecisionPatch(set_stoploss_pending_liquidation=False, set_stoploss_liquidation_target_base=None)
            return Decision(
                intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="stop_loss_no_liquidation"),
                patch=patch,
            )

        swap_action = self._build_liquidation_action(base_to_liquidate)
        if swap_action is None:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_wait_balance"))

        patch = DecisionPatch(set_stoploss_last_liquidation_attempt_ts=now)
        return Decision(
            intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.SUBMIT_SWAP, reason="stop_loss_liquidation"),
            actions=[swap_action],
            patch=patch,
        )

    def _decide_rebalance_stops(self, snapshot: Snapshot) -> Optional[Decision]:
        current_price = snapshot.current_price
        now = snapshot.now
        stop_actions: List[ExecutorAction] = []
        patch = DecisionPatch()

        for lp_view in snapshot.active_lp:
            if lp_view.executor_id in self._ctx.rebalance.plans:
                continue
            if lp_view.in_transition:
                continue

            lower_price = lp_view.lower_price
            upper_price = lp_view.upper_price
            if lower_price is None or upper_price is None or lower_price <= 0 or upper_price <= 0:
                continue

            effective_price = current_price if current_price is not None else lp_view.current_price
            if effective_price is None or effective_price <= 0:
                continue

            if lower_price <= effective_price <= upper_price:
                patch.clear_out_of_range_since.add(lp_view.executor_id)
                continue

            deviation_pct = self._out_of_range_deviation_pct(effective_price, lower_price, upper_price)
            if deviation_pct < self.config.hysteresis_pct:
                continue

            out_of_range_since = lp_view.out_of_range_since
            if out_of_range_since is None:
                out_of_range_since = self._ctx.rebalance.out_of_range_since.get(lp_view.executor_id)
                if out_of_range_since is None:
                    out_of_range_since = now
                patch.update_out_of_range_since[lp_view.executor_id] = out_of_range_since
            else:
                patch.update_out_of_range_since[lp_view.executor_id] = out_of_range_since

            if out_of_range_since is None:
                continue
            if (now - out_of_range_since) < self.config.rebalance_seconds:
                continue
            if (now - self._ctx.rebalance.last_rebalance_ts) < self.config.cooldown_seconds:
                continue
            if not self._can_rebalance_now(now):
                continue

            fee_rate_ewma = self._ctx.lp.get(lp_view.executor_id, LpContext()).fee.fee_rate_ewma
            allow_rebalance, cost_details = evaluate_cost_filter(
                enabled=self.config.cost_filter_enabled,
                current_price=effective_price,
                position_value=self._estimate_position_value(lp_view, effective_price),
                fee_rate_ewma=fee_rate_ewma,
                fee_rate_bootstrap_quote_per_hour=self.config.cost_filter_fee_rate_bootstrap_quote_per_hour,
                position_width_pct=self.config.position_width_pct,
                auto_swap_enabled=self.config.auto_swap_enabled,
                swap_slippage_pct=self.config.swap_slippage_pct,
                fixed_cost_quote=self.config.cost_filter_fixed_cost_quote,
                max_payback_sec=self.config.cost_filter_max_payback_sec,
            )
            if not allow_rebalance and should_force_rebalance(now, out_of_range_since, self.config.rebalance_seconds):
                cost_details["reason"] = "force_rebalance"
                allow_rebalance = True
            if self.config.cost_filter_enabled:
                self._maybe_log_cost_filter(allow_rebalance, cost_details, now)
            if not allow_rebalance:
                continue

            stop_actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=lp_view.executor_id))
            patch.add_rebalance_plans[lp_view.executor_id] = RebalancePlan(reopen_after_ts=now + self.config.reopen_delay_sec)
            patch.record_rebalance_ts = now

        if not stop_actions:
            return None
        return Decision(
            intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.STOP_LP, reason="out_of_range_rebalance"),
            actions=stop_actions,
            patch=patch,
        )

    def _out_of_range_deviation_pct(self, price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        if price < lower:
            return (lower - price) / lower * Decimal("100")
        if price > upper:
            return (price - upper) / upper * Decimal("100")
        return Decimal("0")

    def _can_rebalance_now(self, now: float) -> bool:
        if self.config.max_rebalances_per_hour <= 0:
            return True
        while self._ctx.rebalance.timestamps and (now - self._ctx.rebalance.timestamps[0] > 3600):
            self._ctx.rebalance.timestamps.popleft()
        return len(self._ctx.rebalance.timestamps) < self.config.max_rebalances_per_hour

    def _decide_rebalance_reopen(self, snapshot: Snapshot) -> Optional[Decision]:
        if self._ctx.swap.awaiting_balance_refresh:
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="wait_balance_refresh"))

        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="price_unavailable"))

        eligible_ids = [
            executor_id
            for executor_id, plan in self._ctx.rebalance.plans.items()
            if snapshot.now >= plan.reopen_after_ts and not (snapshot.lp.get(executor_id) and snapshot.lp[executor_id].is_active)
        ]
        if not eligible_ids:
            return None

        executor_id = sorted(eligible_ids, key=lambda i: self._ctx.rebalance.plans[i].reopen_after_ts)[0]

        delta, reason = self._compute_inventory_delta(current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if delta is None:
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason=reason or "insufficient_balance"))
        delta_base, delta_quote_value = delta

        swap_plan = self._maybe_plan_inventory_swap(
            now=snapshot.now,
            current_price=current_price,
            delta_base=delta_base,
            delta_quote_value=delta_quote_value,
            flow=IntentFlow.REBALANCE,
            log_entry=False,
        )
        if swap_plan is not None:
            return swap_plan

        action, open_amounts = self._build_open_lp_action(current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if action is None:
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="budget_unavailable"))

        patch = DecisionPatch()
        patch.clear_rebalance_plans.add(executor_id)
        patch.set_lp_open_amounts[action.executor_config.id] = open_amounts
        return Decision(
            intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.SUBMIT_LP, reason="rebalance_open"),
            actions=[action],
            patch=patch,
        )

    def _decide_entry(self, snapshot: Snapshot) -> Decision:
        now = snapshot.now

        if snapshot.active_lp:
            return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.WAIT, reason="lp_active"))

        if not self._is_entry_triggered(snapshot.current_price):
            return Decision(
                intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.NONE, reason="idle"),
                entry_log=EntryLog(reason="entry_not_triggered", current_price=snapshot.current_price),
            )

        if not self.config.reenter_enabled and self._ctx.stoploss.last_exit_reason == "stop_loss":
            return Decision(
                intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="reenter_disabled"),
                entry_log=EntryLog(reason="reenter_disabled", current_price=snapshot.current_price),
            )

        if now < self._ctx.stoploss.until_ts:
            return Decision(
                intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="stoploss_cooldown"),
                entry_log=EntryLog(reason="stoploss_cooldown", current_price=snapshot.current_price),
            )

        if self._ctx.rebalance.plans:
            return Decision(
                intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="rebalance_pending"),
                entry_log=EntryLog(reason="rebalance_pending", current_price=snapshot.current_price),
            )

        if self._ctx.swap.awaiting_balance_refresh:
            return Decision(
                intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="wait_balance_refresh"),
                entry_log=EntryLog(reason="wait_balance_refresh", current_price=snapshot.current_price),
            )

        delta, reason = self._compute_inventory_delta(snapshot.current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if delta is None:
            return Decision(
                intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason=reason or "insufficient_balance"),
                entry_log=EntryLog(reason=reason or "insufficient_balance", current_price=snapshot.current_price),
            )
        delta_base, delta_quote_value = delta

        swap_plan = self._maybe_plan_inventory_swap(
            now=now,
            current_price=snapshot.current_price,
            delta_base=delta_base,
            delta_quote_value=delta_quote_value,
            flow=IntentFlow.ENTRY,
            log_entry=True,
        )
        if swap_plan is not None:
            return swap_plan

        action, open_amounts = self._build_open_lp_action(snapshot.current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if action is None:
            return Decision(
                intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="budget_unavailable"),
                entry_log=EntryLog(reason="budget_unavailable", current_price=snapshot.current_price),
            )

        patch = DecisionPatch()
        patch.set_lp_open_amounts[action.executor_config.id] = open_amounts
        return Decision(
            intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.SUBMIT_LP, reason="entry_open"),
            actions=[action],
            entry_log=EntryLog(reason="lp_open", current_price=snapshot.current_price, details={"base": open_amounts[0], "quote": open_amounts[1]}),
            patch=patch,
        )

    def _is_entry_triggered(self, current_price: Optional[Decimal]) -> bool:
        if self.config.target_price <= 0:
            return True
        if current_price is None:
            return False
        if self.config.trigger_above:
            return current_price >= self.config.target_price
        return current_price <= self.config.target_price

    def _build_position_budget(self, current_price: Optional[Decimal]) -> Optional[PositionBudget]:
        if current_price is None or current_price <= 0:
            return None
        total_value = max(Decimal("0"), self.config.position_value_quote)
        if total_value <= 0:
            return None
        ratio = Decimal(str(self.config.target_base_value_pct))
        base_value = total_value * ratio
        quote_value = total_value - base_value
        base_amount = base_value / current_price
        return PositionBudget(
            total_value_quote=total_value,
            target_base=base_amount,
            target_quote=quote_value,
        )

    def _compute_inventory_delta(
        self,
        current_price: Optional[Decimal],
        wallet_base: Decimal,
        wallet_quote: Decimal,
    ) -> Tuple[Optional[Tuple[Decimal, Decimal]], Optional[str]]:
        if current_price is None or current_price <= 0:
            return None, "price_unavailable"
        budget = self._build_position_budget(current_price)
        if budget is None:
            return None, "budget_unavailable"
        total_value = wallet_base * current_price + wallet_quote
        if total_value < budget.total_value_quote:
            return None, "insufficient_balance"

        target_base = budget.target_base
        target_quote = budget.target_quote
        base_deficit = max(Decimal("0"), target_base - wallet_base)
        quote_deficit = max(Decimal("0"), target_quote - wallet_quote)
        if base_deficit > 0 and quote_deficit > 0:
            return None, "insufficient_balance"

        delta_base = Decimal("0")
        if base_deficit > 0:
            quote_surplus = max(Decimal("0"), wallet_quote - target_quote)
            if quote_surplus <= 0:
                return None, "insufficient_balance"
            delta_base = min(base_deficit, quote_surplus / current_price)
        elif quote_deficit > 0:
            base_surplus = max(Decimal("0"), wallet_base - target_base)
            if base_surplus <= 0:
                return None, "insufficient_balance"
            delta_base = -min(base_surplus, quote_deficit / current_price)

        delta_quote_value = abs(delta_base * current_price)
        return (delta_base, delta_quote_value), None

    def _maybe_plan_inventory_swap(
        self,
        *,
        now: float,
        current_price: Optional[Decimal],
        delta_base: Decimal,
        delta_quote_value: Decimal,
        flow: IntentFlow,
        log_entry: bool,
    ) -> Optional[Decision]:
        min_swap_value = self._swap_min_quote_value()
        if delta_quote_value <= 0 or delta_quote_value < min_swap_value:
            return None

        if not self.config.auto_swap_enabled:
            entry_log = (
                EntryLog(
                    reason="swap_required_auto_swap_disabled",
                    current_price=current_price,
                    details={"delta_quote_value": delta_quote_value, "min_swap_value": min_swap_value},
                )
                if log_entry
                else None
            )
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_required"), entry_log=entry_log)

        if self.config.cooldown_seconds > 0 and (now - self._ctx.swap.last_inventory_swap_ts) < self.config.cooldown_seconds:
            entry_log = (
                EntryLog(
                    reason="swap_cooldown",
                    current_price=current_price,
                    details={"cooldown_seconds": self.config.cooldown_seconds},
                )
                if log_entry
                else None
            )
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_cooldown"), entry_log=entry_log)

        swap_action = self._build_inventory_swap_action(current_price, delta_base, delta_quote_value)
        if swap_action is None:
            entry_log = EntryLog(reason="swap_required_no_action", current_price=current_price) if log_entry else None
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_required"), entry_log=entry_log)

        patch = DecisionPatch(set_swap_last_inventory_swap_ts=now, set_swap_inventory_swap_failed=None)
        reason = "entry_inventory" if flow == IntentFlow.ENTRY else "rebalance_inventory"
        entry_log = None
        if log_entry:
            entry_log = EntryLog(
                reason="entry_inventory_swap",
                current_price=current_price,
                details={"delta_base": delta_base, "delta_quote_value": delta_quote_value},
            )
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.SUBMIT_SWAP, reason=reason),
            actions=[swap_action],
            entry_log=entry_log,
            patch=patch,
        )

    def _swap_min_quote_value(self) -> Decimal:
        min_pct = max(Decimal("0"), self.config.swap_min_value_pct)
        return self.config.position_value_quote * min_pct

    def _build_inventory_swap_action(
        self,
        current_price: Optional[Decimal],
        delta_base: Decimal,
        delta_quote_value: Decimal,
    ) -> Optional[CreateExecutorAction]:
        if current_price is None or current_price <= 0:
            return None
        if delta_quote_value < self._swap_min_quote_value():
            return None

        if delta_base > 0:
            amount = self._apply_swap_buffer(delta_quote_value)
            if amount <= 0:
                return None
            side = TradeType.BUY
            amount_in_is_quote = True
        elif delta_base < 0:
            amount = self._apply_swap_buffer(abs(delta_base))
            if amount <= 0:
                return None
            side = TradeType.SELL
            amount_in_is_quote = False
        else:
            return None

        executor_config = GatewaySwapExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.router_connector,
            trading_pair=self.config.trading_pair,
            side=side,
            amount=amount,
            amount_in_is_quote=amount_in_is_quote,
            slippage_pct=self.config.swap_slippage_pct,
            pool_address=self.config.pool_address or None,
            timeout_sec=self.config.swap_timeout_sec,
            poll_interval_sec=self.config.swap_poll_interval_sec,
            max_retries=self.config.swap_retry_attempts,
            retry_delay_sec=self.config.swap_retry_delay_sec,
            level_id="inventory",
            budget_key=self._budget_key,
        )
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _build_liquidation_action(self, base_amount: Decimal) -> Optional[CreateExecutorAction]:
        if base_amount <= 0:
            return None
        swap_amount = self._apply_swap_buffer(base_amount)
        if swap_amount <= 0:
            return None
        executor_config = GatewaySwapExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.router_connector,
            trading_pair=self.config.trading_pair,
            side=TradeType.SELL,
            amount=swap_amount,
            amount_in_is_quote=False,
            slippage_pct=self.config.swap_slippage_pct,
            pool_address=self.config.pool_address or None,
            timeout_sec=self.config.swap_timeout_sec,
            poll_interval_sec=self.config.swap_poll_interval_sec,
            max_retries=self.config.swap_retry_attempts,
            retry_delay_sec=self.config.swap_retry_delay_sec,
            level_id="liquidate",
            budget_key=self._budget_key,
        )
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _apply_swap_buffer(self, amount: Decimal) -> Decimal:
        buffer_pct = max(Decimal("0"), self.config.swap_safety_buffer_pct)
        if buffer_pct <= 0:
            return amount
        return amount * (Decimal("1") - (buffer_pct / Decimal("100")))

    def _build_open_lp_action(
        self,
        current_price: Optional[Decimal],
        wallet_base: Decimal,
        wallet_quote: Decimal,
    ) -> Tuple[Optional[CreateExecutorAction], Optional[Tuple[Decimal, Decimal]]]:
        _, amounts, _ = self._resolve_open_amounts(current_price, wallet_base, wallet_quote)
        if amounts is None:
            return None, None
        base_amt, quote_amt = amounts
        executor_config = self._create_lp_executor_config(base_amt, quote_amt, current_price)
        if executor_config is None:
            return None, None
        return CreateExecutorAction(controller_id=self.config.id, executor_config=executor_config), (base_amt, quote_amt)

    def _resolve_open_amounts(
        self,
        current_price: Optional[Decimal],
        wallet_base: Decimal,
        wallet_quote: Decimal,
    ) -> Tuple[Optional[PositionBudget], Optional[Tuple[Decimal, Decimal]], Optional[str]]:
        if current_price is None or current_price <= 0:
            return None, None, "price_unavailable"
        budget = self._build_position_budget(current_price)
        if budget is None:
            return None, None, "budget_unavailable"
        total_value = wallet_base * current_price + wallet_quote
        if total_value < budget.total_value_quote:
            return None, None, "insufficient_balance"
        base_amount = min(wallet_base, budget.target_base)
        quote_amount = min(wallet_quote, budget.target_quote)
        if base_amount <= 0 and quote_amount <= 0:
            return None, None, "insufficient_balance"
        return budget, (base_amount, quote_amount), None

    def _create_lp_executor_config(
        self,
        base_amt: Decimal,
        quote_amt: Decimal,
        current_price: Optional[Decimal],
    ) -> Optional[LPPositionExecutorConfig]:
        if current_price is None or current_price <= 0:
            return None
        if base_amt <= 0 and quote_amt <= 0:
            return None

        lower_price, upper_price = self._calculate_price_bounds(current_price, base_amt, quote_amt)
        lp_base_amt, lp_quote_amt = self._tokens.strategy_amounts_to_lp(base_amt, quote_amt)
        lp_lower_price, lp_upper_price = self._tokens.strategy_bounds_to_lp(lower_price, upper_price)

        side = self._get_side_from_amounts(lp_base_amt, lp_quote_amt)
        executor_config = LPPositionExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            pool_address=self.config.pool_address,
            trading_pair=self._tokens.pool_trading_pair,
            base_token=self._tokens.pool_base_token,
            quote_token=self._tokens.pool_quote_token,
            lower_price=lp_lower_price,
            upper_price=lp_upper_price,
            base_amount=lp_base_amt,
            quote_amount=lp_quote_amt,
            side=side,
            keep_position=False,
            budget_key=self._budget_key,
        )
        reservation_id = self._reserve_budget(base_amt, quote_amt)
        if reservation_id is None:
            return None
        executor_config.budget_reservation_id = reservation_id
        return executor_config

    def _calculate_price_bounds(
        self,
        current_price: Decimal,
        base_amt: Decimal,
        quote_amt: Decimal,
    ) -> Tuple[Decimal, Decimal]:
        total_width = self.config.position_width_pct / Decimal("100")
        if base_amt > 0 and quote_amt > 0:
            half_width = total_width / Decimal("2")
            lower_price = current_price * (Decimal("1") - half_width)
            upper_price = current_price * (Decimal("1") + half_width)
        elif base_amt > 0:
            lower_price = current_price
            upper_price = current_price * (Decimal("1") + total_width)
        elif quote_amt > 0:
            lower_price = current_price * (Decimal("1") - total_width)
            upper_price = current_price
        else:
            half_width = total_width / Decimal("2")
            lower_price = current_price * (Decimal("1") - half_width)
            upper_price = current_price * (Decimal("1") + half_width)
        return lower_price, upper_price

    @staticmethod
    def _get_side_from_amounts(base_amt: Decimal, quote_amt: Decimal) -> int:
        if base_amt > 0 and quote_amt > 0:
            return 0
        if quote_amt > 0:
            return 1
        return 2

    def _reserve_budget(self, base_amt: Decimal, quote_amt: Decimal) -> Optional[str]:
        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is None:
            self.logger().warning(
                "Budget reserve failed: connector unavailable (base=%.6f quote=%.6f)",
                float(base_amt),
                float(quote_amt),
            )
            return None
        requirements: Dict[str, Decimal] = {}
        if base_amt > 0:
            requirements[self._tokens.base_token] = base_amt
        if quote_amt > 0:
            requirements[self._tokens.quote_token] = quote_amt
        reservation_id = self._budget_coordinator.reserve(
            connector_name=self.config.connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self.config.native_token_symbol,
            min_native_balance=self.config.min_native_balance,
        )
        if reservation_id is None:
            self.logger().warning(
                "Budget reserve failed: insufficient balance (base=%.6f quote=%.6f)",
                float(base_amt),
                float(quote_amt),
            )
        return reservation_id

    def _maybe_log_cost_filter(self, allowed: bool, details: Dict, now: float):
        interval = max(self.config.cooldown_seconds, 60)
        if (now - self._last_cost_filter_log_ts) < interval:
            return
        self._last_cost_filter_log_ts = now
        reason = details.get("reason", "unknown")
        self.logger().info(
            "Cost filter %s: reason=%s fee_rate=%.8f(%s) in_range=%.2f(%s) "
            "widths=%.4f/%.4f expected=%.6f cost=%.6f fixed=%.6f swap_notional=%.6f "
            "swap_cost=%.6f payback=%.2f",
            "ALLOW" if allowed else "BLOCK",
            reason,
            float(details.get("fee_rate", Decimal("0"))),
            details.get("fee_rate_source", "n/a"),
            float(details.get("in_range_time", Decimal("0"))),
            details.get("in_range_source", "n/a"),
            float(details.get("lower_width", Decimal("0"))),
            float(details.get("upper_width", Decimal("0"))),
            float(details.get("expected_fees", Decimal("0"))),
            float(details.get("cost", Decimal("0"))),
            float(details.get("fixed_cost", Decimal("0"))),
            float(details.get("swap_notional", Decimal("0"))),
            float(details.get("swap_cost", Decimal("0"))),
            float(details.get("payback_sec", Decimal("0"))),
        )

    def _derive_view_state(self, snapshot: Snapshot, intent: Intent) -> Tuple[ControllerState, Optional[str]]:
        if self.config.manual_kill_switch:
            return ControllerState.MANUAL_STOP, "manual_kill_switch"

        if self._ctx.failure.blocked:
            return ControllerState.LP_FAILURE, self._ctx.failure.reason or intent.reason

        if snapshot.active_swaps:
            label = snapshot.active_swaps[0].level_id or "swap"
            return ControllerState.WAIT_SWAP, f"{label}_in_progress"

        if intent.flow == IntentFlow.MANUAL and intent.stage == IntentStage.STOP_LP:
            return ControllerState.MANUAL_STOP, intent.reason

        if intent.stage == IntentStage.STOP_LP:
            if intent.flow == IntentFlow.STOPLOSS:
                return ControllerState.STOPLOSS_PAUSE, intent.reason
            if intent.flow == IntentFlow.REBALANCE:
                return ControllerState.REBALANCE_WAIT_CLOSE, intent.reason
            if intent.flow == IntentFlow.FAILURE:
                return ControllerState.LP_FAILURE, intent.reason

        if intent.stage == IntentStage.SUBMIT_SWAP:
            if intent.flow == IntentFlow.STOPLOSS:
                return ControllerState.WAIT_SWAP, intent.reason
            return ControllerState.INVENTORY_SWAP, intent.reason

        if intent.stage == IntentStage.SUBMIT_LP:
            return ControllerState.READY_TO_OPEN, intent.reason

        if self._ctx.stoploss.pending_liquidation or snapshot.now < self._ctx.stoploss.until_ts:
            return ControllerState.STOPLOSS_PAUSE, intent.reason

        if self._ctx.rebalance.plans:
            return ControllerState.REBALANCE_WAIT_CLOSE, intent.reason

        if snapshot.active_lp:
            return ControllerState.ACTIVE, intent.reason

        return ControllerState.IDLE, intent.reason

    def _apply_patch(self, patch: DecisionPatch):
        if patch.set_failure_reason is not None:
            self._ctx.failure.blocked = True
            self._ctx.failure.reason = patch.set_failure_reason

        if patch.clear_rebalance_all:
            self._ctx.rebalance.plans.clear()

        if patch.add_rebalance_plans:
            self._ctx.rebalance.plans.update(patch.add_rebalance_plans)

        for executor_id in patch.clear_rebalance_plans:
            self._ctx.rebalance.plans.pop(executor_id, None)

        for executor_id in patch.clear_out_of_range_since:
            self._ctx.rebalance.out_of_range_since.pop(executor_id, None)

        for executor_id, ts in patch.update_out_of_range_since.items():
            self._ctx.rebalance.out_of_range_since[executor_id] = ts

        if patch.record_rebalance_ts is not None:
            self._ctx.rebalance.last_rebalance_ts = patch.record_rebalance_ts
            self._ctx.rebalance.timestamps.append(patch.record_rebalance_ts)

        if patch.set_stoploss_until_ts is not None:
            self._ctx.stoploss.until_ts = patch.set_stoploss_until_ts
        if patch.set_stoploss_last_exit_reason is not None:
            self._ctx.stoploss.last_exit_reason = patch.set_stoploss_last_exit_reason
        if patch.set_stoploss_pending_liquidation is not None:
            self._ctx.stoploss.pending_liquidation = patch.set_stoploss_pending_liquidation
        if patch.set_stoploss_liquidation_target_base is not None or patch.set_stoploss_pending_liquidation is False:
            self._ctx.stoploss.liquidation_target_base = patch.set_stoploss_liquidation_target_base
        if patch.set_stoploss_last_liquidation_attempt_ts is not None:
            self._ctx.stoploss.last_liquidation_attempt_ts = patch.set_stoploss_last_liquidation_attempt_ts

        if patch.set_swap_last_inventory_swap_ts is not None:
            self._ctx.swap.last_inventory_swap_ts = patch.set_swap_last_inventory_swap_ts
        if patch.set_swap_inventory_swap_failed is not None or patch.set_swap_last_inventory_swap_ts is not None:
            self._ctx.swap.inventory_swap_failed = patch.set_swap_inventory_swap_failed

        for executor_id, (base_amt, quote_amt) in patch.set_lp_open_amounts.items():
            ctx = self._ctx.lp.setdefault(executor_id, LpContext())
            ctx.open_base = base_amt
            ctx.open_quote = quote_amt

    def _reconcile_done_swaps(self, now: float):
        for executor in self.executors_info:
            if executor.type != "gateway_swap_executor":
                continue
            if executor.controller_id != self.config.id:
                continue
            if not executor.is_done:
                continue
            if executor.id in self._ctx.swap.settled_executor_ids:
                continue
            self._ctx.swap.settled_executor_ids.add(executor.id)

            level_id = getattr(executor.config, "level_id", None)
            if level_id == "liquidate":
                self._ctx.stoploss.last_liquidation_attempt_ts = now
                if executor.close_type == CloseType.COMPLETED:
                    self._ctx.swap.awaiting_balance_refresh = True
                    self._ctx.stoploss.pending_liquidation = False
                    self._ctx.stoploss.liquidation_target_base = None
                else:
                    self._ctx.stoploss.pending_liquidation = True
            elif level_id == "inventory":
                if executor.close_type == CloseType.COMPLETED:
                    self._ctx.swap.inventory_swap_failed = False
                    self._ctx.swap.awaiting_balance_refresh = True
                else:
                    self._ctx.swap.inventory_swap_failed = True
