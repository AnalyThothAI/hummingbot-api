import logging
from decimal import Decimal
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

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

from .clmm_lp_domain.cost_filter import CostFilter
from .clmm_lp_domain.components import (
    BudgetAnchor,
    ControllerContext,
    ControllerState,
    Decision,
    DecisionPatch,
    Intent,
    IntentFlow,
    IntentStage,
    LPView,
    LpContext,
    PositionBudget,
    Regions,
    RebalancePlan,
    RebalanceStage,
    Snapshot,
    SwapView,
    TokenOrderMapper,
)

Rule = Callable[[Snapshot, ControllerContext, Regions], Optional[Decision]]


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
    swap_slippage_pct: Decimal = Field(default=Decimal("1"), json_schema_extra={"is_updatable": True})

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

    @field_validator("pool_address", mode="before")
    @classmethod
    def validate_pool_address(cls, v):
        value = str(v or "").strip()
        if not value:
            raise ValueError("pool_address is required")
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
        self._latest_snapshot: Optional[Snapshot] = None
        self._rules: List[Rule] = [
            self._rule_manual_kill_switch,
            self._rule_failure_blocked,
            self._rule_detect_lp_failure,
            self._rule_swap_in_progress_gate,
            self._rule_stoploss_trigger,
            self._rule_rebalance_stop,
            self._rule_lp_active_gate,
            self._rule_stoploss_liquidation,
            self._rule_stoploss_cooldown,
            self._rule_rebalance_reopen_or_wait,
            self._rule_entry,
        ]

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")
        self._last_balance_update_ts: float = 0.0

        rate_connector = self.config.router_connector
        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=rate_connector,
                trading_pair=self.config.trading_pair,
            ),
        ])

    async def update_processed_data(self):
        now = self.market_data_provider.time()

        self._reconcile_done_swaps(now)

        await self._update_wallet_balances(now)

        snapshot = self._build_snapshot(now)

        self._ensure_anchors(snapshot)
        self._update_fee_rate_estimates(snapshot)
        self._latest_snapshot = snapshot
        self.processed_data.update({
            "current_price": snapshot.current_price,
            "wallet_base": snapshot.wallet_base,
            "wallet_quote": snapshot.wallet_quote,
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
        })

    def determine_executor_actions(self) -> List[ExecutorAction]:
        snapshot = self._latest_snapshot
        if snapshot is None:
            snapshot = self._build_snapshot(self.market_data_provider.time())
        self._latest_snapshot = None

        self._reconcile_done_swaps(snapshot.now)
        self._reconcile_rebalance_plans(snapshot)

        decision = self._decide(snapshot)
        self._ctx.apply(decision.patch)

        derived_state, reason = self._derive_view_state(snapshot, decision.intent)

        self.processed_data.update({
            "current_price": snapshot.current_price,
            "wallet_base": snapshot.wallet_base,
            "wallet_quote": snapshot.wallet_quote,
            "controller_state": derived_state.value,
            "state_reason": reason,
            "intent_flow": decision.intent.flow.value,
            "intent_stage": decision.intent.stage.value,
            "intent_reason": decision.intent.reason,
            "pending_liquidation": self._ctx.stoploss.pending_liquidation,
            "stop_loss_active": snapshot.now < self._ctx.stoploss.until_ts,
            "stop_loss_until_ts": self._ctx.stoploss.until_ts if self._ctx.stoploss.until_ts > 0 else None,
            "rebalance_pending": len(self._ctx.rebalance.plans),
            "rebalance_plans": [
                {
                    "executor_id": executor_id,
                    "stage": plan.stage.value,
                    "reopen_after_ts": plan.reopen_after_ts,
                    "open_executor_id": plan.open_executor_id,
                }
                for executor_id, plan in self._ctx.rebalance.plans.items()
            ],
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
        })

        return decision.actions

    def _build_snapshot(self, now: float) -> Snapshot:
        current_price = self._get_current_price()

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
            wallet_base=self._wallet_base,
            wallet_quote=self._wallet_quote,
            lp=lp,
            swaps=swaps,
        )

    def _parse_lp_view(self, executor: ExecutorInfo) -> LPView:
        custom = executor.custom_info or {}
        lp_base_amount = Decimal(str(custom.get("base_amount", 0)))
        lp_quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        lp_base_fee = Decimal(str(custom.get("base_fee", 0)))
        lp_quote_fee = Decimal(str(custom.get("quote_fee", 0)))

        inverted = self._tokens.executor_token_order_inverted(executor)
        if inverted is None:
            inverted = self._tokens.pool_order_inverted
        base_amount, quote_amount = self._tokens.lp_amounts_to_strategy(lp_base_amount, lp_quote_amount, inverted)
        base_fee, quote_fee = self._tokens.lp_amounts_to_strategy(lp_base_fee, lp_quote_fee, inverted)

        lower = custom.get("lower_price")
        upper = custom.get("upper_price")
        price = custom.get("current_price")
        lower = Decimal(str(lower)) if lower is not None else None
        upper = Decimal(str(upper)) if upper is not None else None
        price = Decimal(str(price)) if price is not None else None
        if lower is not None and upper is not None:
            lower, upper = self._tokens.lp_bounds_to_strategy(lower, upper, inverted)
        if price is not None:
            price = self._tokens.lp_price_to_strategy(price, inverted)
        out_of_range_since = custom.get("out_of_range_since")
        out_of_range_since = float(out_of_range_since) if out_of_range_since is not None else None

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
            out_of_range_since=out_of_range_since,
        )

    def _get_current_price(self) -> Optional[Decimal]:
        price = self.market_data_provider.get_rate(self.config.trading_pair)
        if price is None:
            return None
        return Decimal(str(price))

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
        except Exception:
            return

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
            if lp_view.state != LPPositionStates.IN_RANGE.value:
                continue
            position_address = lp_view.position_address or ""
            if not position_address:
                continue
            pending_fee_quote = lp_view.base_fee * current_price + lp_view.quote_fee
            ctx = self._ctx.lp.setdefault(lp_view.executor_id, LpContext()).fee
            CostFilter.update_fee_rate_ewma(
                now=snapshot.now,
                position_address=position_address,
                pending_fee_quote=pending_fee_quote,
                ctx=ctx,
            )

    def _reconcile_rebalance_plans(self, snapshot: Snapshot):
        now = snapshot.now
        for executor_id, plan in list(self._ctx.rebalance.plans.items()):
            if plan.stage == RebalanceStage.OPEN_REQUESTED:
                open_id = plan.open_executor_id
                open_lp = snapshot.lp.get(open_id) if open_id else None
                if open_lp and (
                    open_lp.position_address
                    or open_lp.state in {LPPositionStates.IN_RANGE.value, LPPositionStates.OUT_OF_RANGE.value}
                ):
                    self._ctx.rebalance.plans.pop(executor_id, None)
                    continue
                if plan.requested_at_ts > 0 and (now - plan.requested_at_ts) > 30.0:
                    self.logger().warning(
                        "Rebalance open request timed out: old_executor=%s expected_new_executor=%s",
                        executor_id,
                        open_id,
                    )
                    if open_id:
                        self._ctx.lp.pop(open_id, None)
                    self._ctx.rebalance.plans[executor_id] = RebalancePlan(
                        stage=RebalanceStage.WAIT_REOPEN,
                        reopen_after_ts=max(plan.reopen_after_ts, now),
                        requested_at_ts=now,
                    )
            elif plan.stage == RebalanceStage.STOP_REQUESTED:
                old_lp = snapshot.lp.get(executor_id)
                if old_lp is None or not old_lp.is_active:
                    self._ctx.rebalance.plans[executor_id] = RebalancePlan(
                        stage=RebalanceStage.WAIT_REOPEN,
                        reopen_after_ts=plan.reopen_after_ts,
                        requested_at_ts=plan.requested_at_ts,
                    )
            elif plan.stage == RebalanceStage.WAIT_REOPEN:
                old_lp = snapshot.lp.get(executor_id)
                if old_lp is not None and old_lp.is_active:
                    self._ctx.rebalance.plans[executor_id] = RebalancePlan(
                        stage=RebalanceStage.STOP_REQUESTED,
                        reopen_after_ts=plan.reopen_after_ts,
                        requested_at_ts=plan.requested_at_ts if plan.requested_at_ts > 0 else now,
                    )

    def _decide(self, snapshot: Snapshot) -> Decision:
        regions = self._compute_regions(snapshot)
        for rule in self._rules:
            decision = rule(snapshot, self._ctx, regions)
            if decision is not None:
                return decision
        return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.NONE, reason="idle"))

    def _compute_regions(self, snapshot: Snapshot) -> Regions:
        label = snapshot.active_swaps[0].level_id if snapshot.active_swaps else None
        price_ok = snapshot.current_price is not None and snapshot.current_price > 0
        return Regions(
            manual_stop=bool(self.config.manual_kill_switch),
            failure_blocked=bool(self._ctx.failure.blocked),
            has_active_swaps=bool(snapshot.active_swaps),
            active_swap_label=label,
            has_active_lp=bool(snapshot.active_lp),
            price_ok=price_ok,
            awaiting_balance_refresh=bool(self._ctx.swap.awaiting_balance_refresh),
            stoploss_cooldown_active=snapshot.now < self._ctx.stoploss.until_ts,
            stoploss_pending_liquidation=bool(self._ctx.stoploss.pending_liquidation),
            rebalance_pending=bool(self._ctx.rebalance.plans),
            rebalance_open_in_progress=any(
                plan.stage == RebalanceStage.OPEN_REQUESTED for plan in self._ctx.rebalance.plans.values()
            ),
            entry_triggered=self._is_entry_triggered(snapshot.current_price),
            reenter_blocked=(
                (not self.config.reenter_enabled) and self._ctx.stoploss.last_exit_reason == "stop_loss"
            ),
        )

    def _rule_manual_kill_switch(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.manual_stop:
            return None
        actions = [StopExecutorAction(controller_id=self.config.id, executor_id=v.executor_id) for v in snapshot.active_lp]
        return Decision(
            intent=Intent(flow=IntentFlow.MANUAL, stage=IntentStage.STOP_LP, reason="manual_kill_switch"),
            actions=actions,
        )

    def _rule_failure_blocked(
        self,
        _: Snapshot,
        __: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.failure_blocked:
            return None
        return Decision(
            intent=Intent(flow=IntentFlow.FAILURE, stage=IntentStage.WAIT, reason=self._ctx.failure.reason or "lp_failure"),
        )

    def _rule_detect_lp_failure(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
        detected = self._detect_lp_failure()
        if detected is None:
            return None
        failed_id, reason = detected
        actions: List[ExecutorAction] = []
        failed_view = snapshot.lp.get(failed_id)
        if failed_view is not None and failed_view.is_active:
            actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=failed_id))
        patch = DecisionPatch()
        patch.failure.set_reason = reason
        patch.rebalance.clear_all = True
        patch.stoploss.pending_liquidation = False
        self.logger().error("LP executor failure detected (%s). Manual intervention required.", reason)
        return Decision(
            intent=Intent(flow=IntentFlow.FAILURE, stage=IntentStage.STOP_LP, reason=reason),
            actions=actions,
            patch=patch,
        )

    def _rule_swap_in_progress_gate(
        self,
        _: Snapshot,
        __: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.has_active_swaps:
            return None
        label = regions.active_swap_label or "swap"
        if label == "liquidate":
            flow = IntentFlow.STOPLOSS
        elif regions.rebalance_pending:
            flow = IntentFlow.REBALANCE
        else:
            flow = IntentFlow.ENTRY
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.WAIT, reason=f"{label}_in_progress"),
        )

    def _rule_stoploss_trigger(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
        return self._decide_stoploss(snapshot)

    def _rule_rebalance_stop(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
        return self._decide_rebalance_stops(snapshot)

    def _rule_lp_active_gate(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if regions.has_active_lp and not regions.rebalance_pending:
            return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.WAIT, reason="lp_active"))
        return None

    def _rule_stoploss_liquidation(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.stoploss_pending_liquidation:
            return None
        return self._decide_liquidation(snapshot)

    def _rule_stoploss_cooldown(
        self,
        _: Snapshot,
        __: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.stoploss_cooldown_active:
            return None
        return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="cooldown"))

    def _rule_rebalance_reopen_or_wait(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.rebalance_pending:
            return None
        rebalance_open = self._decide_rebalance_reopen(snapshot)
        if rebalance_open is not None:
            return rebalance_open
        return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="rebalance_wait"))

    def _rule_entry(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
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
        patch = DecisionPatch()
        patch.rebalance.clear_all = True
        patch.stoploss.last_exit_reason = "stop_loss"
        patch.stoploss.until_ts = snapshot.now + self.config.stop_loss_pause_sec
        if self._stop_loss_liquidation_mode == StopLossLiquidationMode.QUOTE:
            patch.stoploss.pending_liquidation = True
            patch.stoploss.last_liquidation_attempt_ts = 0.0
            patch.stoploss.liquidation_target_base = self._compute_liquidation_target_base(snapshot, snapshot.active_lp)
        return Decision(
            intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.STOP_LP, reason="stop_loss_triggered"),
            actions=actions,
            patch=patch,
        )

    def _compute_liquidation_target_base(self, snapshot: Snapshot, stopped: List[LPView]) -> Optional[Decimal]:
        total: Decimal = Decimal("0")
        for lp_view in stopped:
            total += max(Decimal("0"), lp_view.base_amount + lp_view.base_fee)
        return total

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
            expected_base = max(Decimal("0"), target or Decimal("0"))
            if snapshot.wallet_base <= 0 and expected_base > 0:
                patch = DecisionPatch()
                patch.swap.awaiting_balance_refresh = True
                return Decision(
                    intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_wait_balance"),
                    patch=patch,
                )
            patch = DecisionPatch()
            patch.stoploss.pending_liquidation = False
            return Decision(
                intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="stop_loss_no_liquidation"),
                patch=patch,
            )

        swap_action = self._build_liquidation_action(base_to_liquidate)
        if swap_action is None:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_wait_balance"))

        patch = DecisionPatch()
        patch.stoploss.last_liquidation_attempt_ts = now
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

        # Idempotent stop: once STOP_REQUESTED, keep emitting StopExecutorAction until the LP closes.
        for executor_id, plan in self._ctx.rebalance.plans.items():
            if plan.stage != RebalanceStage.STOP_REQUESTED:
                continue
            lp_view = snapshot.lp.get(executor_id)
            if lp_view is None or not lp_view.is_active:
                continue
            if lp_view.in_transition:
                continue
            stop_actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=executor_id))

        # Fresh out-of-range detection: request a rebalance stop and create a STOP_REQUESTED plan.
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
                continue

            deviation_pct = self._out_of_range_deviation_pct(effective_price, lower_price, upper_price)
            if deviation_pct < self.config.hysteresis_pct:
                continue

            out_of_range_since = lp_view.out_of_range_since
            if out_of_range_since is None:
                continue
            if (now - out_of_range_since) < self.config.rebalance_seconds:
                continue
            if (now - self._ctx.rebalance.last_rebalance_ts) < self.config.cooldown_seconds:
                continue
            if not self._can_rebalance_now(now):
                continue

            fee_rate_ewma = self._ctx.lp.get(lp_view.executor_id, LpContext()).fee.fee_rate_ewma
            allow_rebalance = CostFilter.allow_rebalance(
                enabled=self.config.cost_filter_enabled,
                position_value=self._estimate_position_value(lp_view, effective_price),
                fee_rate_ewma=fee_rate_ewma,
                fee_rate_bootstrap_quote_per_hour=self.config.cost_filter_fee_rate_bootstrap_quote_per_hour,
                auto_swap_enabled=self.config.auto_swap_enabled,
                swap_slippage_pct=self.config.swap_slippage_pct,
                fixed_cost_quote=self.config.cost_filter_fixed_cost_quote,
                max_payback_sec=self.config.cost_filter_max_payback_sec,
            )
            if not allow_rebalance and CostFilter.should_force_rebalance(
                now=now,
                out_of_range_since=out_of_range_since,
                rebalance_seconds=self.config.rebalance_seconds,
            ):
                allow_rebalance = True
            if not allow_rebalance:
                continue

            stop_actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=lp_view.executor_id))
            patch.rebalance.add_plans[lp_view.executor_id] = RebalancePlan(
                stage=RebalanceStage.STOP_REQUESTED,
                reopen_after_ts=now + self.config.reopen_delay_sec,
                requested_at_ts=now,
            )
            patch.rebalance.record_rebalance_ts = now

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

        if any(plan.stage == RebalanceStage.OPEN_REQUESTED for plan in self._ctx.rebalance.plans.values()):
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="open_in_progress"))

        eligible_ids = [
            executor_id
            for executor_id, plan in self._ctx.rebalance.plans.items()
            if plan.stage == RebalanceStage.WAIT_REOPEN
            and plan.open_executor_id is None
            and snapshot.now >= plan.reopen_after_ts
            and not (snapshot.lp.get(executor_id) and snapshot.lp[executor_id].is_active)
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
        )
        if swap_plan is not None:
            return swap_plan

        action = self._build_open_lp_action(current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if action is None:
            return Decision(intent=Intent(flow=IntentFlow.REBALANCE, stage=IntentStage.WAIT, reason="budget_unavailable"))

        prev_plan = self._ctx.rebalance.plans.get(executor_id)
        reopen_after_ts = prev_plan.reopen_after_ts if prev_plan is not None else snapshot.now
        patch = DecisionPatch()
        patch.rebalance.add_plans[executor_id] = RebalancePlan(
            stage=RebalanceStage.OPEN_REQUESTED,
            reopen_after_ts=reopen_after_ts,
            open_executor_id=action.executor_config.id,
            requested_at_ts=snapshot.now,
        )
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
            return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.NONE, reason="idle"))

        if not self.config.reenter_enabled and self._ctx.stoploss.last_exit_reason == "stop_loss":
            return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="reenter_disabled"))

        if now < self._ctx.stoploss.until_ts:
            return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="stoploss_cooldown"))

        if self._ctx.rebalance.plans:
            return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="rebalance_pending"))

        if self._ctx.swap.awaiting_balance_refresh:
            return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="wait_balance_refresh"))

        delta, reason = self._compute_inventory_delta(snapshot.current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if delta is None:
            return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason=reason or "insufficient_balance"))
        delta_base, delta_quote_value = delta

        swap_plan = self._maybe_plan_inventory_swap(
            now=now,
            current_price=snapshot.current_price,
            delta_base=delta_base,
            delta_quote_value=delta_quote_value,
            flow=IntentFlow.ENTRY,
        )
        if swap_plan is not None:
            return swap_plan

        action = self._build_open_lp_action(snapshot.current_price, snapshot.wallet_base, snapshot.wallet_quote)
        if action is None:
            return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="budget_unavailable"))

        return Decision(
            intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.SUBMIT_LP, reason="entry_open"),
            actions=[action],
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
    ) -> Optional[Decision]:
        min_swap_value = self._swap_min_quote_value()
        if delta_quote_value <= 0 or delta_quote_value < min_swap_value:
            return None

        if not self.config.auto_swap_enabled:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_required"))

        if self.config.cooldown_seconds > 0 and (now - self._ctx.swap.last_inventory_swap_ts) < self.config.cooldown_seconds:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_cooldown"))

        swap_action = self._build_inventory_swap_action(current_price, delta_base, delta_quote_value)
        if swap_action is None:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_required"))

        patch = DecisionPatch()
        patch.swap.last_inventory_swap_ts = now
        reason = "entry_inventory" if flow == IntentFlow.ENTRY else "rebalance_inventory"
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.SUBMIT_SWAP, reason=reason),
            actions=[swap_action],
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
    ) -> Optional[CreateExecutorAction]:
        _, amounts, _ = self._resolve_open_amounts(current_price, wallet_base, wallet_quote)
        if amounts is None:
            return None
        base_amt, quote_amt = amounts
        executor_config = self._create_lp_executor_config(base_amt, quote_amt, current_price)
        if executor_config is None:
            return None
        return CreateExecutorAction(controller_id=self.config.id, executor_config=executor_config)

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
        return reservation_id

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
                    target = self._ctx.stoploss.liquidation_target_base
                    if target is None:
                        self._ctx.stoploss.pending_liquidation = True
                    else:
                        sold = Decimal(str(getattr(executor.config, "amount", 0)))
                        remaining = max(Decimal("0"), target - max(Decimal("0"), sold))
                        if remaining <= 0:
                            self._ctx.stoploss.pending_liquidation = False
                            self._ctx.stoploss.liquidation_target_base = None
                        else:
                            self._ctx.stoploss.pending_liquidation = True
                            self._ctx.stoploss.liquidation_target_base = remaining
                else:
                    self._ctx.stoploss.pending_liquidation = True
            elif level_id == "inventory":
                if executor.close_type == CloseType.COMPLETED:
                    self._ctx.swap.awaiting_balance_refresh = True
