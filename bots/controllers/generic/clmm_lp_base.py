import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

from pydantic import Field

from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.budget.budget_coordinator import BudgetCoordinatorRegistry
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction, StopExecutorAction

from .clmm_lp_domain.cost_filter import CostFilter
from .clmm_lp_domain.components import (
    BudgetAnchor,
    ControllerContext,
    Decision,
    DecisionPatch,
    Intent,
    IntentFlow,
    IntentStage,
    LPView,
    LpContext,
    Regions,
    RebalanceStage,
    Snapshot,
    SwapView,
    SwapPurpose,
    PoolDomainAdapter,
)
from .clmm_lp_domain.runtime import ActionFactory, BalanceManager, SnapshotBuilder
from .clmm_lp_domain.open_planner import OpenProposal, plan_open
from .clmm_lp_domain.rebalance_engine import RebalanceEngine
from .clmm_lp_domain.policies import CLMMPolicyBase
from .clmm_lp_domain.v3_math import V3Math

Rule = Callable[[Snapshot, ControllerContext, Regions], Optional[Decision]]
ReconcileHandler = Callable[[Snapshot], None]


class ControllerMode(str, Enum):
    MANUAL = "MANUAL"
    FAILURE = "FAILURE"
    STOPLOSS = "STOPLOSS"
    REBALANCE = "REBALANCE"
    ENTRY = "ENTRY"
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"


@dataclass(frozen=True)
class RuleSpec:
    name: str
    handler: Rule
    mode: Optional[ControllerMode] = None
    description: str = ""


@dataclass(frozen=True)
class ReconcileStep:
    name: str
    handler: ReconcileHandler
    description: str = ""


class StopLossLiquidationMode(str, Enum):
    NONE = "none"
    QUOTE = "quote"


class CLMMLPBaseConfig(ControllerConfigBase):
    controller_type: str = "generic"
    controller_name: str = "clmm_lp_base"
    connector_name: str = ""
    router_connector: str = ""
    trading_pair: str = ""
    pool_trading_pair: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    pool_address: str = ""

    target_price: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    trigger_above: bool = Field(default=True, json_schema_extra={"is_updatable": True})

    position_value_quote: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})

    position_width_pct: Decimal = Field(default=Decimal("12"), json_schema_extra={"is_updatable": True})
    rebalance_seconds: int = Field(default=60, json_schema_extra={"is_updatable": True})
    hysteresis_pct: Decimal = Field(default=Decimal("0.002"), json_schema_extra={"is_updatable": True})
    cooldown_seconds: int = Field(default=30, json_schema_extra={"is_updatable": True})
    max_rebalances_per_hour: int = Field(default=20, json_schema_extra={"is_updatable": True})
    reopen_delay_sec: int = Field(default=5, json_schema_extra={"is_updatable": True})
    rebalance_open_timeout_sec: int = Field(default=120, json_schema_extra={"is_updatable": True})

    auto_swap_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})
    swap_min_value_pct: Decimal = Field(default=Decimal("0.005"), json_schema_extra={"is_updatable": True})
    swap_safety_buffer_pct: Decimal = Field(default=Decimal("0.02"), json_schema_extra={"is_updatable": True})
    swap_slippage_pct: Decimal = Field(default=Decimal("0.01"), json_schema_extra={"is_updatable": True})
    max_inventory_swap_attempts: int = Field(default=3, json_schema_extra={"is_updatable": True})
    max_stoploss_liquidation_attempts: int = Field(default=5, json_schema_extra={"is_updatable": True})

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
    balance_update_timeout_sec: int = Field(default=10, json_schema_extra={"is_updatable": True})
    balance_refresh_interval_sec: int = Field(default=20, json_schema_extra={"is_updatable": True})
    balance_refresh_timeout_sec: int = Field(default=30, json_schema_extra={"is_updatable": True})

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pool_pair = self.pool_trading_pair or self.trading_pair
        markets = markets.add_or_update(self.connector_name, pool_pair)
        markets = markets.add_or_update(self.router_connector, self.trading_pair)
        return markets


class CLMMLPBaseController(ControllerBase):
    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: CLMMLPBaseConfig, policy: CLMMPolicyBase, domain: PoolDomainAdapter, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: CLMMLPBaseConfig = config
        self._policy = policy

        self._domain = domain

        self._budget_key = self.config.budget_key or self.config.id
        self._budget_coordinator = BudgetCoordinatorRegistry.get(self._budget_key)
        self._stop_loss_liquidation_mode = self.config.stop_loss_liquidation_mode

        self._ctx = ControllerContext()
        self._balance_manager = BalanceManager(
            config=self.config,
            domain=self._domain,
            market_data_provider=self.market_data_provider,
            ctx=self._ctx,
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
            config=self.config,
            domain=self._domain,
            market_data_provider=self.market_data_provider,
        )
        self._latest_snapshot: Optional[Snapshot] = None
        self._rebalance_engine = RebalanceEngine(
            controller_id=self.config.id,
            config=self.config,
            estimate_position_value=self._estimate_position_value,
            out_of_range_deviation_pct=self._out_of_range_deviation_pct,
            can_rebalance_now=self._can_rebalance_now,
            swap_slippage_pct=self._action_factory.swap_slippage_pct,
            build_open_proposal=self._build_open_proposal,
            maybe_plan_inventory_swap=self._maybe_plan_inventory_swap,
            build_open_lp_action=self._action_factory.build_open_lp_action,
        )
        self._rules: List[RuleSpec] = [
            RuleSpec(
                name="manual_kill",
                handler=self._rule_manual_kill_switch,
                mode=ControllerMode.MANUAL,
                description="manual kill switch",
            ),
            RuleSpec(
                name="failure_blocked",
                handler=self._rule_failure_blocked,
                mode=ControllerMode.FAILURE,
                description="blocked on failure",
            ),
            RuleSpec(
                name="detect_lp_failure",
                handler=self._rule_detect_lp_failure,
                mode=ControllerMode.FAILURE,
                description="lp executor failure",
            ),
            RuleSpec(
                name="swap_concurrency_guard",
                handler=self._rule_swap_concurrency_guard,
                description="ensure single active swap",
            ),
            RuleSpec(
                name="stoploss_trigger",
                handler=self._rule_stoploss_trigger,
                mode=ControllerMode.STOPLOSS,
                description="stoploss trigger",
            ),
            RuleSpec(
                name="swap_in_progress_gate",
                handler=self._rule_swap_in_progress_gate,
                description="gate while swap active",
            ),
            RuleSpec(
                name="rebalance_stop",
                handler=self._rule_rebalance_stop,
                mode=ControllerMode.REBALANCE,
                description="stop lp for rebalance",
            ),
            RuleSpec(
                name="lp_active_gate",
                handler=self._rule_lp_active_gate,
                mode=ControllerMode.ACTIVE,
                description="active lp gate",
            ),
            RuleSpec(
                name="wait_balance_refresh",
                handler=self._rule_wait_balance_refresh,
                description="wait balance refresh",
            ),
            RuleSpec(
                name="stoploss_liquidation",
                handler=self._rule_stoploss_liquidation,
                mode=ControllerMode.STOPLOSS,
                description="stoploss liquidation",
            ),
            RuleSpec(
                name="stoploss_cooldown",
                handler=self._rule_stoploss_cooldown,
                mode=ControllerMode.STOPLOSS,
                description="stoploss cooldown",
            ),
            RuleSpec(
                name="rebalance_reopen_or_wait",
                handler=self._rule_rebalance_reopen_or_wait,
                mode=ControllerMode.REBALANCE,
                description="rebalance reopen",
            ),
            RuleSpec(
                name="reenter_blocked",
                handler=self._rule_reenter_blocked,
                mode=ControllerMode.ENTRY,
                description="reenter blocked",
            ),
            RuleSpec(
                name="entry",
                handler=self._rule_entry,
                mode=ControllerMode.ENTRY,
                description="entry open",
            ),
        ]
        # Order matters: balance sync/settlement before plan transitions and cleanup.
        self._reconcile_steps: List[ReconcileStep] = [
            ReconcileStep(
                name="clear_stale_refresh",
                handler=self._reconcile_clear_stale_refresh,
                description="expire balance sync barrier",
            ),
            ReconcileStep(
                name="done_swaps",
                handler=self._reconcile_done_swaps,
                description="apply swap settlement",
            ),
            ReconcileStep(
                name="lp_balance_events",
                handler=self._reconcile_lp_balance_events,
                description="apply lp balance events",
            ),
            ReconcileStep(
                name="rebalance_plans",
                handler=self._reconcile_rebalance_plans,
                description="advance rebalance FSM",
            ),
            ReconcileStep(
                name="anchors",
                handler=self._ensure_anchors,
                description="ensure budget anchors",
            ),
            ReconcileStep(
                name="fee_rates",
                handler=self._update_fee_rate_estimates,
                description="update fee ewma",
            ),
            ReconcileStep(
                name="stoploss_latch",
                handler=self._reconcile_stoploss_latch,
                description="reset stoploss latch",
            ),
            ReconcileStep(
                name="cleanup",
                handler=self._cleanup_ctx,
                description="prune stale contexts",
            ),
        ]

        self._last_policy_update_ts: float = 0.0
        self._policy_update_interval_sec: float = 600.0
        self._policy_bootstrap_interval_sec: float = 30.0
        self._policy_update_timeout_sec: float = 10.0
        self._policy_update_task: Optional[asyncio.Task] = None
        self._last_mode: ControllerMode = ControllerMode.IDLE
        self._last_rule: str = "idle"

        rate_connector = self.config.router_connector
        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=rate_connector,
                trading_pair=self.config.trading_pair,
            ),
        ])

    # Lifecycle
    async def update_processed_data(self):
        now = self.market_data_provider.time()

        self._balance_manager.schedule_refresh(now)

        snapshot = self._build_snapshot(now)
        self._latest_snapshot = snapshot
        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is not None:
            interval = self._policy_bootstrap_interval_sec
            if self._policy.is_ready():
                interval = self._policy_update_interval_sec
            if (now - self._last_policy_update_ts) >= interval:
                if self._policy_update_task is None or self._policy_update_task.done():
                    self._last_policy_update_ts = now
                    self._policy_update_task = safe_ensure_future(self._safe_policy_update(connector))
                    self._policy_update_task.add_done_callback(self._clear_policy_update_task)
        return

    def _clear_policy_update_task(self, task: asyncio.Task) -> None:
        if self._policy_update_task is task:
            self._policy_update_task = None

    async def _safe_policy_update(self, connector) -> None:
        try:
            await asyncio.wait_for(self._policy.update(connector), timeout=self._policy_update_timeout_sec)
        except Exception:
            self.logger().exception(
                "policy.update failed | connector=%s",
                self.config.connector_name,
            )

    def determine_executor_actions(self) -> List[ExecutorAction]:
        snapshot = self._latest_snapshot
        if snapshot is None:
            snapshot = self._build_snapshot(self.market_data_provider.time())
        self._latest_snapshot = None

        self._reconcile(snapshot)

        decision = self._decide(snapshot)
        self._ctx.apply(decision.patch)
        self._log_decision_actions(decision)

        return decision.actions

    def get_custom_info(self) -> Dict:
        snapshot = self._latest_snapshot or self._build_snapshot(self.market_data_provider.time())
        nav = self._compute_nav(snapshot)
        if nav is None:
            return {}
        nav_value, lp_value, wallet_value, budget_value = nav
        current_price = snapshot.current_price

        def _as_float(value: Optional[Decimal]) -> Optional[float]:
            return float(value) if value is not None else None

        def _build_lp_item(lp_view: LPView) -> Dict:
            lp_ctx = self._ctx.lp.get(lp_view.executor_id)
            anchor = lp_ctx.anchor if lp_ctx is not None else None
            fee_ctx = lp_ctx.fee if lp_ctx is not None else None
            position_value = None
            in_range = None
            if current_price is not None:
                position_value = self._estimate_position_value(lp_view, current_price)
                if lp_view.lower_price is not None and lp_view.upper_price is not None:
                    in_range = lp_view.lower_price <= current_price <= lp_view.upper_price
            lp_item = {
                "executor_id": lp_view.executor_id,
                "lower_price": _as_float(lp_view.lower_price),
                "upper_price": _as_float(lp_view.upper_price),
                "in_range": in_range,
                "position_value_quote": _as_float(position_value),
            }
            if anchor is not None:
                lp_item["anchor_value_quote"] = float(anchor.value_quote)
                lp_item["anchor_wallet_base"] = float(anchor.wallet_base_amount)
                lp_item["anchor_wallet_quote"] = float(anchor.wallet_quote_amount)
                if self.config.stop_loss_pnl_pct > 0:
                    trigger_value = anchor.value_quote - (anchor.value_quote * self.config.stop_loss_pnl_pct)
                    lp_item["stoploss_trigger_quote"] = float(trigger_value)
            if fee_ctx is not None and fee_ctx.fee_rate_ewma is not None:
                fee_rate_per_hour = fee_ctx.fee_rate_ewma * Decimal("3600")
                lp_item["fee_rate_ewma_quote_per_hour"] = float(fee_rate_per_hour)
            if lp_view.out_of_range_since is not None:
                lp_item["out_of_range_since"] = lp_view.out_of_range_since
            return lp_item

        info = {
            "nav_quote": float(nav_value),
            "nav_lp_quote": float(lp_value),
            "nav_wallet_quote": float(wallet_value),
            "wallet_base": float(snapshot.wallet_base),
            "wallet_quote": float(snapshot.wallet_quote),
            "active_lp_count": len(snapshot.active_lp),
            "active_swap_count": len(snapshot.active_swaps),
            "awaiting_balance_refresh": self._ctx.swap.awaiting_balance_refresh,
        }
        if current_price is not None:
            info["price"] = float(current_price)
        if budget_value > 0:
            info["nav_budget_quote"] = float(budget_value)
        info["mode"] = self._last_mode.value
        info["mode_rule"] = self._last_rule

        stoploss_cooldown_remaining = self._ctx.stoploss.until_ts - snapshot.now
        if stoploss_cooldown_remaining > 0:
            info["stoploss_cooldown_remaining_sec"] = stoploss_cooldown_remaining
        info["stoploss_pending_liquidation"] = bool(self._ctx.stoploss.pending_liquidation)
        info["rebalance_plan_count"] = len(self._ctx.rebalance.plans)

        if snapshot.active_lp:
            info["active_lp"] = [_build_lp_item(lp_view) for lp_view in snapshot.active_lp]

        if self._ctx.rebalance.plans:
            info["rebalance_plans"] = [
                {
                    "executor_id": executor_id,
                    "stage": plan.stage.value,
                    "reopen_after_sec": max(0.0, plan.reopen_after_ts - snapshot.now),
                }
                for executor_id, plan in self._ctx.rebalance.plans.items()
            ]
        return info

    def _log_decision_actions(self, decision: Decision) -> None:
        if not decision.actions:
            return
        self.logger().info(
            "Decision %s/%s %s | actions=%s",
            decision.intent.flow.value,
            decision.intent.stage.value,
            decision.intent.reason or "",
            len(decision.actions),
        )

    # Snapshot & pricing
    def _build_snapshot(self, now: float) -> Snapshot:
        return self._snapshot_builder.build(
            now=now,
            executors_info=self.executors_info,
            wallet_base=self._balance_manager.wallet_base,
            wallet_quote=self._balance_manager.wallet_quote,
        )

    def _compute_nav(self, snapshot: Snapshot) -> Optional[Tuple[Decimal, Decimal, Decimal, Decimal]]:
        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return None
        lp_value = sum(self._estimate_position_value(lp, current_price) for lp in snapshot.active_lp)
        wallet_value = snapshot.wallet_base * current_price + snapshot.wallet_quote
        budget_value = max(Decimal("0"), self.config.position_value_quote)
        if budget_value <= 0:
            nav_value = lp_value + wallet_value
        else:
            unallocated_value = max(Decimal("0"), budget_value - lp_value)
            nav_value = lp_value + min(wallet_value, unallocated_value)
        return nav_value, lp_value, wallet_value, budget_value

    def _ensure_anchors(self, snapshot: Snapshot):
        price = snapshot.current_price
        if price is None or price <= 0:
            return
        if self._ctx.swap.awaiting_balance_refresh:
            return
        for lp_view in snapshot.active_lp:
            if lp_view.in_transition or not lp_view.position_address:
                continue
            lp_ctx = self._ctx.lp.setdefault(lp_view.executor_id, LpContext())
            anchor = lp_ctx.anchor
            if anchor is not None and anchor.value_quote > 0:
                continue
            if self._estimate_position_value(lp_view, price) <= 0:
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
            ctx = self._ctx.lp.setdefault(lp_view.executor_id, LpContext()).fee
            CostFilter.update_fee_rate_ewma(
                now=snapshot.now,
                position_address=position_address,
                base_fee=lp_view.base_fee,
                quote_fee=lp_view.quote_fee,
                price=current_price,
                ctx=ctx,
            )

    # Decisions & rules
    def _decide(self, snapshot: Snapshot) -> Decision:
        regions = self._compute_regions(snapshot)
        for rule in self._rules:
            decision = rule.handler(snapshot, self._ctx, regions)
            if decision is not None:
                self._record_decision(rule, decision)
                return decision
        decision = Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.NONE, reason="idle"))
        self._last_mode = ControllerMode.IDLE
        self._last_rule = "idle"
        return decision

    @staticmethod
    def _mode_from_flow(flow: IntentFlow) -> ControllerMode:
        if flow == IntentFlow.MANUAL:
            return ControllerMode.MANUAL
        if flow == IntentFlow.FAILURE:
            return ControllerMode.FAILURE
        if flow == IntentFlow.STOPLOSS:
            return ControllerMode.STOPLOSS
        if flow == IntentFlow.REBALANCE:
            return ControllerMode.REBALANCE
        if flow == IntentFlow.ENTRY:
            return ControllerMode.ENTRY
        return ControllerMode.IDLE

    def _record_decision(self, rule: RuleSpec, decision: Decision) -> None:
        mode = rule.mode or self._mode_from_flow(decision.intent.flow)
        self._last_mode = mode
        self._last_rule = rule.name

    def _compute_regions(self, snapshot: Snapshot) -> Regions:
        label = self._select_active_swap_label(snapshot.active_swaps)
        return Regions(
            manual_stop=bool(self.config.manual_kill_switch),
            failure_blocked=bool(self._ctx.failure.blocked),
            has_active_swaps=bool(snapshot.active_swaps),
            active_swap_label=label,
            has_active_lp=bool(snapshot.active_lp),
            awaiting_balance_refresh=bool(self._ctx.swap.awaiting_balance_refresh),
            stoploss_cooldown_active=snapshot.now < self._ctx.stoploss.until_ts,
            stoploss_pending_liquidation=bool(self._ctx.stoploss.pending_liquidation),
            rebalance_pending=bool(self._ctx.rebalance.plans),
            entry_triggered=self._is_entry_triggered(snapshot.current_price),
            reenter_blocked=(
                (not self.config.reenter_enabled) and self._ctx.stoploss.last_exit_reason == "stop_loss"
            ),
        )

    @staticmethod
    def _select_active_swap_label(active_swaps: List[SwapView]) -> Optional[str]:
        swap = CLMMLPBaseController._select_swap_to_keep(active_swaps)
        return swap.level_id if swap is not None else None

    @staticmethod
    def _select_swap_to_keep(active_swaps: List[SwapView]) -> Optional[SwapView]:
        if not active_swaps:
            return None
        for purpose in (SwapPurpose.STOPLOSS, SwapPurpose.INVENTORY):
            for swap in active_swaps:
                if swap.purpose == purpose:
                    return swap
        return min(active_swaps, key=lambda swap: swap.executor_id)

    def _rule_swap_concurrency_guard(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if len(snapshot.active_swaps) <= 1:
            return None
        keep = self._select_swap_to_keep(snapshot.active_swaps)
        if keep is None:
            return None
        actions = [
            StopExecutorAction(controller_id=self.config.id, executor_id=swap.executor_id)
            for swap in snapshot.active_swaps
            if swap.executor_id != keep.executor_id
        ]
        if not actions:
            return None
        if keep.purpose == SwapPurpose.STOPLOSS:
            flow = IntentFlow.STOPLOSS
        elif regions.rebalance_pending:
            flow = IntentFlow.REBALANCE
        else:
            flow = IntentFlow.ENTRY
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="concurrent_swaps"),
            actions=actions,
        )

    def _build_stop_actions(self, snapshot: Snapshot) -> List[StopExecutorAction]:
        actions: List[StopExecutorAction] = []
        seen: set = set()
        for view in list(snapshot.active_lp) + list(snapshot.active_swaps):
            if view.executor_id in seen:
                continue
            actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=view.executor_id))
            seen.add(view.executor_id)
        return actions

    def _rule_manual_kill_switch(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.manual_stop:
            return None
        actions = self._build_stop_actions(snapshot)
        return Decision(
            intent=Intent(flow=IntentFlow.MANUAL, stage=IntentStage.STOP_LP, reason="manual_kill_switch"),
            actions=actions,
        )

    def _rule_failure_blocked(
        self,
        _: Snapshot,
        ctx: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.failure_blocked:
            return None
        return Decision(
            intent=Intent(flow=IntentFlow.FAILURE, stage=IntentStage.WAIT, reason=ctx.failure.reason or "lp_failure"),
        )

    def _rule_detect_lp_failure(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
        detected = self._detect_lp_failure(snapshot)
        if detected is None:
            return None
        failed_id, reason = detected
        actions: List[ExecutorAction] = self._build_stop_actions(snapshot)
        patch = DecisionPatch()
        patch.failure.set_reason = reason
        patch.rebalance.clear_all = True
        patch.stoploss.pending_liquidation = False
        self.logger().error(
            "LP executor failure detected | executor_id=%s reason=%s. Manual intervention required.",
            failed_id,
            reason,
        )
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
        ctx: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
        return self._decide_stoploss(snapshot, ctx)

    def _rule_rebalance_stop(
        self,
        snapshot: Snapshot,
        ctx: ControllerContext,
        __: Regions,
    ) -> Optional[Decision]:
        return self._rebalance_engine.decide_stop(snapshot, ctx)

    def _rule_lp_active_gate(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if regions.has_active_lp and not regions.rebalance_pending:
            return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.WAIT, reason="lp_active"))
        return None

    def _rule_wait_balance_refresh(
        self,
        _: Snapshot,
        __: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.awaiting_balance_refresh:
            return None
        if regions.rebalance_pending:
            flow = IntentFlow.REBALANCE
        elif regions.stoploss_pending_liquidation:
            flow = IntentFlow.STOPLOSS
        else:
            flow = IntentFlow.ENTRY
        return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="wait_balance_refresh"))

    def _rule_stoploss_liquidation(
        self,
        snapshot: Snapshot,
        ctx: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.stoploss_pending_liquidation:
            return None
        return self._decide_liquidation(snapshot, ctx)

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
        ctx: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.rebalance_pending:
            return None
        return self._rebalance_engine.decide_reopen_or_wait(snapshot, ctx)

    def _rule_reenter_blocked(
        self,
        _: Snapshot,
        __: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.reenter_blocked:
            return None
        return Decision(intent=Intent(flow=IntentFlow.ENTRY, stage=IntentStage.WAIT, reason="reenter_disabled"))

    def _rule_entry(
        self,
        snapshot: Snapshot,
        ctx: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.entry_triggered:
            return None
        return self._decide_entry(snapshot, ctx)

    def _detect_lp_failure(self, snapshot: Snapshot) -> Optional[Tuple[str, str]]:
        for executor_id, lp_view in snapshot.lp.items():
            state = lp_view.state
            if state == LPPositionStates.RETRIES_EXCEEDED.value:
                return executor_id, "retries_exceeded"
            if lp_view.close_type == CloseType.FAILED:
                return executor_id, "executor_failed"
        return None

    def _decide_stoploss(self, snapshot: Snapshot, ctx: ControllerContext) -> Optional[Decision]:
        if self.config.stop_loss_pnl_pct <= 0:
            return None
        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return None
        if ctx.stoploss.triggered:
            return None

        triggered = False
        for lp_view in snapshot.active_lp:
            lp_ctx = ctx.lp.get(lp_view.executor_id)
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
        patch.stoploss.triggered = True
        patch.stoploss.triggered_ts = snapshot.now
        patch.stoploss.until_ts = snapshot.now + self.config.stop_loss_pause_sec
        if self._stop_loss_liquidation_mode == StopLossLiquidationMode.QUOTE:
            patch.stoploss.pending_liquidation = True
            patch.stoploss.last_liquidation_attempt_ts = 0.0
        return Decision(
            intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.STOP_LP, reason="stop_loss_triggered"),
            actions=actions,
            patch=patch,
        )

    def _decide_liquidation(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        max_attempts = max(0, self.config.max_stoploss_liquidation_attempts)
        if max_attempts > 0 and ctx.stoploss.liquidation_attempts >= max_attempts:
            patch = DecisionPatch()
            patch.failure.set_reason = "stoploss_liquidation_attempts_exhausted"
            patch.stoploss.pending_liquidation = False
            return Decision(
                intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_attempts_exhausted"),
                patch=patch,
            )

        if ctx.stoploss.last_liquidation_attempt_ts > 0 and self.config.cooldown_seconds > 0:
            if (now - ctx.stoploss.last_liquidation_attempt_ts) < self.config.cooldown_seconds:
                return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_cooldown"))

        if ctx.swap.awaiting_balance_refresh:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_wait_balance"))

        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="price_unavailable"))

        target = ctx.stoploss.liquidation_target_base
        if target is None:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_wait_settle"))

        base_to_liquidate = snapshot.wallet_base
        target = max(Decimal("0"), target)
        min_base = self._min_liquidation_base(current_price)
        if target <= min_base:
            patch = DecisionPatch()
            patch.stoploss.pending_liquidation = False
            patch.stoploss.liquidation_target_base = None
            return Decision(
                intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="stop_loss_liquidation_complete"),
                patch=patch,
            )
        base_to_liquidate = min(base_to_liquidate, target)

        if base_to_liquidate <= 0:
            patch = DecisionPatch()
            patch.stoploss.pending_liquidation = False
            return Decision(
                intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="stop_loss_no_liquidation"),
                patch=patch,
            )

        min_value = self._swap_min_quote_value()
        if min_value > 0 and (base_to_liquidate * current_price) < min_value:
            patch = DecisionPatch()
            patch.stoploss.pending_liquidation = False
            patch.stoploss.liquidation_target_base = None
            return Decision(
                intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="stop_loss_liquidation_dust"),
                patch=patch,
            )

        swap_action = self._action_factory.build_swap_action(
            level_id="liquidate",
            now=now,
            side=TradeType.SELL,
            amount=base_to_liquidate,
            amount_in_is_quote=False,
            apply_buffer=False,
        )
        if swap_action is None:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_wait_balance"))

        patch = DecisionPatch()
        patch.stoploss.last_liquidation_attempt_ts = now
        patch.stoploss.liquidation_attempts = ctx.stoploss.liquidation_attempts + 1
        return Decision(
            intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.SUBMIT_SWAP, reason="stop_loss_liquidation"),
            actions=[swap_action],
            patch=patch,
        )

    def _out_of_range_deviation_pct(self, price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        if price < lower:
            return (lower - price) / lower * Decimal("100")
        if price > upper:
            return (price - upper) / upper * Decimal("100")
        return Decimal("0")

    def _can_rebalance_now(self, now: float, ctx: ControllerContext) -> bool:
        if self.config.max_rebalances_per_hour <= 0:
            return True
        while ctx.rebalance.timestamps and (now - ctx.rebalance.timestamps[0] > 3600):
            ctx.rebalance.timestamps.popleft()
        return len(ctx.rebalance.timestamps) < self.config.max_rebalances_per_hour

    def _decide_entry(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        return plan_open(
            snapshot=snapshot,
            ctx=ctx,
            flow=IntentFlow.ENTRY,
            reason="entry_open",
            build_open_proposal=self._build_open_proposal,
            maybe_plan_inventory_swap=self._maybe_plan_inventory_swap,
            build_open_lp_action=self._action_factory.build_open_lp_action,
        )

    def _is_entry_triggered(self, current_price: Optional[Decimal]) -> bool:
        if self.config.target_price <= 0:
            return True
        if current_price is None:
            return False
        if self.config.trigger_above:
            return current_price >= self.config.target_price
        return current_price <= self.config.target_price

    def _build_open_proposal(
        self,
        current_price: Optional[Decimal],
        wallet_base: Decimal,
        wallet_quote: Decimal,
    ) -> Tuple[Optional[OpenProposal], Optional[str]]:
        if current_price is None or current_price <= 0:
            return None, "price_unavailable"
        total_value = max(Decimal("0"), self.config.position_value_quote)
        if total_value <= 0:
            return None, "budget_unavailable"
        range_plan = self._policy.range_plan(current_price)
        if range_plan is None:
            return None, "range_unavailable"
        ratio = self._policy.quote_per_base_ratio(current_price, range_plan.lower, range_plan.upper)
        if ratio is None:
            return None, "ratio_unavailable"
        total_wallet_value = wallet_base * current_price + wallet_quote
        reserve_quote = max(Decimal("0"), self.config.cost_filter_fixed_cost_quote)
        effective_budget = min(total_value, total_wallet_value)
        if reserve_quote > 0:
            effective_budget = max(Decimal("0"), effective_budget - reserve_quote)
        if effective_budget <= 0:
            return None, "insufficient_balance"

        targets = V3Math.target_amounts_from_value(effective_budget, current_price, ratio)
        if targets is None:
            return None, "target_unavailable"
        target_base, target_quote = targets

        open_base = min(wallet_base, target_base)
        open_quote = min(wallet_quote, target_quote)
        if open_base <= 0 and open_quote <= 0:
            return None, "insufficient_balance"

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

        min_pct = max(Decimal("0"), self.config.swap_min_value_pct)
        min_swap_value = effective_budget * min_pct
        delta_quote_value = abs(delta_base * current_price)
        if open_base <= 0 or open_quote <= 0:
            if not self.config.auto_swap_enabled:
                return None, "swap_required"
            if delta_quote_value <= 0 or delta_quote_value < min_swap_value:
                return None, "swap_required"
        return OpenProposal(
            lower=range_plan.lower,
            upper=range_plan.upper,
            target_base=target_base,
            target_quote=target_quote,
            delta_base=delta_base,
            delta_quote_value=delta_quote_value,
            open_base=open_base,
            open_quote=open_quote,
            min_swap_value_quote=min_swap_value,
        ), None

    def _maybe_plan_inventory_swap(
        self,
        *,
        now: float,
        ctx: ControllerContext,
        current_price: Optional[Decimal],
        delta_base: Decimal,
        delta_quote_value: Decimal,
        min_swap_value: Optional[Decimal] = None,
        flow: IntentFlow,
    ) -> Optional[Decision]:
        if min_swap_value is None:
            min_swap_value = self._swap_min_quote_value()
        if delta_quote_value <= 0 or delta_quote_value < min_swap_value:
            return None

        if not self.config.auto_swap_enabled:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_required"))

        max_attempts = max(0, self.config.max_inventory_swap_attempts)
        if max_attempts > 0 and ctx.swap.inventory_swap_attempts >= max_attempts:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_attempts_exhausted"))

        if self.config.cooldown_seconds > 0 and (now - ctx.swap.last_inventory_swap_ts) < self.config.cooldown_seconds:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_cooldown"))

        swap_action = None
        if current_price is not None and current_price > 0:
            if delta_base > 0:
                side = TradeType.BUY
                amount = abs(delta_base * current_price)
                amount_in_is_quote = True
                apply_buffer = False
            elif delta_base < 0:
                side = TradeType.SELL
                amount = abs(delta_base)
                amount_in_is_quote = False
                apply_buffer = True
            else:
                side = None
            if side is not None:
                swap_action = self._action_factory.build_swap_action(
                    level_id="inventory",
                    now=now,
                    side=side,
                    amount=amount,
                    amount_in_is_quote=amount_in_is_quote,
                    apply_buffer=apply_buffer,
                )
        if swap_action is None:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_required"))

        patch = DecisionPatch()
        patch.swap.last_inventory_swap_ts = now
        patch.swap.inventory_swap_attempts = ctx.swap.inventory_swap_attempts + 1
        reason = "entry_inventory" if flow == IntentFlow.ENTRY else "rebalance_inventory"
        return Decision(
            intent=Intent(flow=flow, stage=IntentStage.SUBMIT_SWAP, reason=reason),
            actions=[swap_action],
            patch=patch,
        )

    def _swap_min_quote_value(self) -> Decimal:
        min_pct = max(Decimal("0"), self.config.swap_min_value_pct)
        return self.config.position_value_quote * min_pct

    def _min_liquidation_base(self, current_price: Decimal) -> Decimal:
        if current_price <= 0:
            return Decimal("0")
        min_quote = self._swap_min_quote_value()
        min_base = min_quote / current_price if min_quote > 0 else Decimal("0")
        return max(min_base, Decimal("0.00000001"))

    # Reconcile & cleanup
    def _reconcile(self, snapshot: Snapshot) -> None:
        for step in self._reconcile_steps:
            step.handler(snapshot)

    def _cleanup_ctx(self, snapshot: Snapshot) -> None:
        for executor_id in list(self._ctx.lp.keys()):
            lp_view = snapshot.lp.get(executor_id)
            if lp_view is None or lp_view.is_done:
                self._remove_lp_context(executor_id)
        if self._ctx.swap.settled_executor_ids:
            active_swap_ids = set(snapshot.swaps.keys())
            self._prune_settled_swaps(active_swap_ids)

    def _reconcile_done_swaps(self, snapshot: Snapshot):
        now = snapshot.now
        for swap in snapshot.swaps.values():
            if not swap.is_done:
                continue
            if swap.executor_id in self._ctx.swap.settled_executor_ids:
                continue
            self._mark_swap_settled(swap.executor_id)

            if swap.purpose == SwapPurpose.STOPLOSS:
                self._handle_liquidation_swap(swap, now)
            elif swap.purpose == SwapPurpose.INVENTORY:
                self._handle_inventory_swap(swap, now)

    def _reconcile_lp_balance_events(self, snapshot: Snapshot) -> None:
        liquidation_delta = Decimal("0")
        for executor_id, lp_view in snapshot.lp.items():
            if lp_view.balance_event_seq <= 0:
                continue
            lp_ctx = self._ctx.lp.setdefault(executor_id, LpContext())
            if lp_view.balance_event_seq <= lp_ctx.last_balance_event_seq:
                continue
            self._record_lp_balance_event_seq(lp_ctx, lp_view.balance_event_seq)
            if lp_view.balance_event_base_delta is None or lp_view.balance_event_quote_delta is None:
                self._block_failure(
                    "lp_balance_event_missing",
                    "lp_balance_event_missing | executor_id=%s seq=%s type=%s",
                    executor_id,
                    lp_view.balance_event_seq,
                    lp_view.balance_event_type,
                )
                continue
            self._balance_manager.request_balance_sync(
                now=snapshot.now,
                delta_base=lp_view.balance_event_base_delta,
                delta_quote=lp_view.balance_event_quote_delta,
                reason=f"lp_{lp_view.balance_event_type or 'event'}",
            )
            if self._ctx.stoploss.pending_liquidation and lp_view.balance_event_type == "close":
                if lp_view.balance_event_base_delta > 0:
                    liquidation_delta += lp_view.balance_event_base_delta
        if liquidation_delta > 0:
            patch = DecisionPatch()
            target = self._ctx.stoploss.liquidation_target_base or Decimal("0")
            patch.stoploss.liquidation_target_base = target + liquidation_delta
            self._ctx.apply(patch)

    def _reconcile_stoploss_latch(self, snapshot: Snapshot) -> None:
        if not self._ctx.stoploss.triggered:
            return
        if self._ctx.stoploss.pending_liquidation:
            return
        if snapshot.now < self._ctx.stoploss.until_ts:
            return
        if snapshot.active_lp:
            return
        patch = DecisionPatch()
        patch.stoploss.triggered = False
        patch.stoploss.triggered_ts = 0.0
        self._ctx.apply(patch)

    def _handle_inventory_swap(self, swap: SwapView, now: float) -> None:
        if swap.close_type != CloseType.COMPLETED:
            return
        patch = DecisionPatch()
        patch.swap.inventory_swap_attempts = 0
        self._ctx.apply(patch)
        self._request_swap_balance_sync(swap, now, "inventory_swap")

    def _handle_liquidation_swap(self, swap: SwapView, now: float) -> None:
        patch = DecisionPatch()
        patch.stoploss.last_liquidation_attempt_ts = now
        if swap.close_type != CloseType.COMPLETED:
            patch.stoploss.pending_liquidation = True
            self._ctx.apply(patch)
            return
        self._request_swap_balance_sync(swap, now, "stoploss_swap")
        target = self._ctx.stoploss.liquidation_target_base
        if target is None:
            patch.stoploss.pending_liquidation = True
            self._ctx.apply(patch)
            return
        sold = self._resolve_liquidation_sold_base(swap)
        remaining = max(Decimal("0"), target - sold)
        if remaining <= 0:
            patch.stoploss.pending_liquidation = False
            self._ctx.apply(patch)
            return
        patch.stoploss.pending_liquidation = True
        patch.stoploss.liquidation_target_base = remaining
        self._ctx.apply(patch)

    def _request_swap_balance_sync(self, swap: SwapView, now: float, reason: str) -> None:
        if swap.delta_base is None or swap.delta_quote is None:
            self._block_failure(
                "swap_balance_event_missing",
                "swap_balance_event_missing | executor_id=%s level_id=%s",
                swap.executor_id,
                swap.level_id,
            )
            return
        self._balance_manager.request_balance_sync(
            now=now,
            delta_base=swap.delta_base,
            delta_quote=swap.delta_quote,
            reason=reason,
        )

    def _resolve_liquidation_sold_base(self, swap: SwapView) -> Decimal:
        sold = None
        if swap.executed_amount_base is not None and swap.executed_amount_base > 0:
            sold = swap.executed_amount_base
        elif swap.amount_in is not None and swap.amount_in_is_quote is False and swap.amount_in > 0:
            sold = swap.amount_in
        else:
            sold = swap.amount
        sold = max(Decimal("0"), sold)
        if swap.amount > 0:
            sold = min(sold, swap.amount)
        return sold

    def _reconcile_clear_stale_refresh(self, snapshot: Snapshot) -> None:
        self._balance_manager.clear_stale_refresh(snapshot.now)

    def _reconcile_rebalance_plans(self, snapshot: Snapshot) -> None:
        patch = self._rebalance_engine.reconcile(snapshot, self._ctx)
        if patch is not None:
            self._ctx.apply(patch)

    def _block_failure(self, reason: str, message: str, *args) -> None:
        self.logger().error(message, *args)
        self._ctx.failure.blocked = True
        self._ctx.failure.reason = reason

    def _record_lp_balance_event_seq(self, lp_ctx: LpContext, seq: int) -> None:
        lp_ctx.last_balance_event_seq = seq

    def _mark_swap_settled(self, executor_id: str) -> None:
        self._ctx.swap.settled_executor_ids.add(executor_id)

    def _prune_settled_swaps(self, active_swap_ids: Set[str]) -> None:
        self._ctx.swap.settled_executor_ids.intersection_update(active_swap_ids)

    def _remove_lp_context(self, executor_id: str) -> None:
        self._ctx.lp.pop(executor_id, None)
