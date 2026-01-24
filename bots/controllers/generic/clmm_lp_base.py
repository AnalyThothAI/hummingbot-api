import asyncio
import logging
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import Field, field_validator

from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.logger import HummingbotLogger
from hummingbot.core.utils.async_utils import safe_ensure_future
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
from .clmm_lp_domain.open_planner import OpenProposal, plan_open
from .clmm_lp_domain.rebalance_engine import RebalanceEngine
from .clmm_lp_domain.policies import CLMMPolicyBase
from .clmm_lp_domain.v3_math import V3Math

Rule = Callable[[Snapshot, ControllerContext, Regions], Optional[Decision]]


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

    auto_swap_enabled: bool = Field(default=True, json_schema_extra={"is_updatable": True})
    swap_min_value_pct: Decimal = Field(default=Decimal("0.005"), json_schema_extra={"is_updatable": True})
    swap_safety_buffer_pct: Decimal = Field(default=Decimal("0.02"), json_schema_extra={"is_updatable": True})
    swap_slippage_pct: Decimal = Field(default=Decimal("0.01"), json_schema_extra={"is_updatable": True})

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
    balance_refresh_interval_sec: int = Field(default=20, json_schema_extra={"is_updatable": True})
    balance_refresh_timeout_sec: int = Field(default=30, json_schema_extra={"is_updatable": True})

    @field_validator(
        "hysteresis_pct",
        "swap_min_value_pct",
        "swap_safety_buffer_pct",
        "swap_slippage_pct",
        "stop_loss_pnl_pct",
        mode="after",
    )
    @classmethod
    def validate_ratio_pct(cls, v, info):
        if v is None:
            return v
        if v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        if v >= 1:
            raise ValueError(f"{info.field_name} must be < 1 (use ratio, e.g. 0.01 for 1%)")
        return v

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
        self._latest_snapshot: Optional[Snapshot] = None
        self._rebalance_engine = RebalanceEngine(
            controller_id=self.config.id,
            config=self.config,
            estimate_position_value=self._estimate_position_value,
            out_of_range_deviation_pct=self._out_of_range_deviation_pct,
            can_rebalance_now=self._can_rebalance_now,
            swap_slippage_pct=self._swap_slippage_pct,
            build_open_proposal=self._build_open_proposal,
            maybe_plan_inventory_swap=self._maybe_plan_inventory_swap,
            build_open_lp_action=self._build_open_lp_action,
        )
        self._rules: List[Rule] = [
            self._rule_manual_kill_switch,
            self._rule_failure_blocked,
            self._rule_detect_lp_failure,
            self._rule_swap_in_progress_gate,
            self._rule_stoploss_trigger,
            self._rule_rebalance_stop,
            self._rule_lp_active_gate,
            self._rule_wait_balance_refresh,
            self._rule_stoploss_liquidation,
            self._rule_stoploss_cooldown,
            self._rule_rebalance_reopen_or_wait,
            self._rule_reenter_blocked,
            self._rule_entry,
        ]

        self._wallet_base: Decimal = Decimal("0")
        self._wallet_quote: Decimal = Decimal("0")
        self._last_balance_update_ts: float = 0.0
        self._last_balance_attempt_ts: float = 0.0
        self._wallet_update_task: Optional[asyncio.Task] = None
        self._last_policy_update_ts: float = 0.0
        self._policy_update_interval_sec: float = 600.0
        self._policy_bootstrap_interval_sec: float = 30.0

        rate_connector = self.config.router_connector
        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=rate_connector,
                trading_pair=self.config.trading_pair,
            ),
        ])

    async def update_processed_data(self):
        now = self.market_data_provider.time()

        self._schedule_wallet_balance_refresh(now)

        snapshot = self._build_snapshot(now)
        self._latest_snapshot = snapshot
        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is not None:
            interval = self._policy_bootstrap_interval_sec
            if self._policy.is_ready():
                interval = self._policy_update_interval_sec
            if (now - self._last_policy_update_ts) >= interval:
                await self._policy.update(connector)
                self._last_policy_update_ts = now
        return

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
        info = {
            "nav_quote": float(nav_value),
            "nav_lp_quote": float(lp_value),
            "nav_wallet_quote": float(wallet_value),
        }
        if budget_value > 0:
            info["nav_budget_quote"] = float(budget_value)
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
                custom = executor.custom_info or {}
                executed_amount_base = self._to_decimal(custom.get("executed_amount_base"))
                executed_amount_quote = self._to_decimal(custom.get("executed_amount_quote"))
                amount_in = self._to_decimal(custom.get("amount_in"))
                amount_out = self._to_decimal(custom.get("amount_out"))
                amount_in_is_quote = custom.get("amount_in_is_quote")
                if not isinstance(amount_in_is_quote, bool):
                    amount_in_is_quote = getattr(executor.config, "amount_in_is_quote", None)
                    if not isinstance(amount_in_is_quote, bool):
                        amount_in_is_quote = None
                swaps[executor.id] = SwapView(
                    executor_id=executor.id,
                    is_active=executor.is_active,
                    is_done=executor.is_done,
                    close_type=executor.close_type,
                    level_id=getattr(executor.config, "level_id", None),
                    purpose=self._swap_purpose(getattr(executor.config, "level_id", None)),
                    amount=Decimal(str(getattr(executor.config, "amount", 0))),
                    executed_amount_base=executed_amount_base,
                    executed_amount_quote=executed_amount_quote,
                    amount_in=amount_in,
                    amount_out=amount_out,
                    amount_in_is_quote=amount_in_is_quote,
                )

        active_lp = [v for v in lp.values() if v.is_active]
        active_swaps = [v for v in swaps.values() if v.is_active]
        return Snapshot(
            now=now,
            current_price=current_price,
            wallet_base=self._wallet_base,
            wallet_quote=self._wallet_quote,
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

    def _get_current_price(self) -> Optional[Decimal]:
        price = self.market_data_provider.get_rate(self.config.trading_pair)
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

    def _schedule_wallet_balance_refresh(self, now: float) -> None:
        if self._wallet_update_task is not None and not self._wallet_update_task.done():
            return
        if (now - self._last_balance_attempt_ts) < 1.0:
            return
        if self.config.balance_refresh_interval_sec > 0:
            if not self._ctx.swap.awaiting_balance_refresh and (
                (now - self._last_balance_update_ts) < self.config.balance_refresh_interval_sec
            ):
                return

        connector = self.market_data_provider.connectors.get(self.config.connector_name)
        if connector is None:
            return
        self._last_balance_attempt_ts = now
        self._wallet_update_task = safe_ensure_future(self._update_wallet_balances(connector))
        self._wallet_update_task.add_done_callback(self._clear_wallet_update_task)

    def _clear_wallet_update_task(self, task: asyncio.Task) -> None:
        if self._wallet_update_task is task:
            self._wallet_update_task = None

    async def _update_wallet_balances(self, connector) -> None:
        try:
            await asyncio.wait_for(
                connector.update_balances(),
                timeout=self.config.balance_refresh_timeout_sec,
            )
            self._wallet_base = Decimal(str(connector.get_available_balance(self._domain.base_token) or 0))
            self._wallet_quote = Decimal(str(connector.get_available_balance(self._domain.quote_token) or 0))
            self._last_balance_update_ts = self.market_data_provider.time()
            self._ctx.swap.awaiting_balance_refresh = False
            self._ctx.swap.awaiting_balance_refresh_since = 0.0
        except Exception:
            self.logger().exception("update_balances failed")

    def _clear_stale_balance_refresh(self, now: float) -> None:
        if not self._ctx.swap.awaiting_balance_refresh:
            return
        if self._ctx.swap.awaiting_balance_refresh_since <= 0:
            self._ctx.swap.awaiting_balance_refresh_since = now
            return
        if (now - self._ctx.swap.awaiting_balance_refresh_since) < self.config.balance_refresh_timeout_sec:
            return
        self.logger().warning("awaiting_balance_refresh timeout exceeded; clearing.")
        self._ctx.swap.awaiting_balance_refresh = False
        self._ctx.swap.awaiting_balance_refresh_since = 0.0

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
            ctx = self._ctx.lp.setdefault(lp_view.executor_id, LpContext()).fee
            CostFilter.update_fee_rate_ewma(
                now=snapshot.now,
                position_address=position_address,
                base_fee=lp_view.base_fee,
                quote_fee=lp_view.quote_fee,
                price=current_price,
                ctx=ctx,
            )

    def _decide(self, snapshot: Snapshot) -> Decision:
        regions = self._compute_regions(snapshot)
        for rule in self._rules:
            decision = rule(snapshot, self._ctx, regions)
            if decision is not None:
                return decision
        return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.NONE, reason="idle"))

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
            rebalance_open_in_progress=any(
                plan.stage == RebalanceStage.OPEN_REQUESTED for plan in self._ctx.rebalance.plans.values()
            ),
            entry_triggered=self._is_entry_triggered(snapshot.current_price),
            reenter_blocked=(
                (not self.config.reenter_enabled) and self._ctx.stoploss.last_exit_reason == "stop_loss"
            ),
        )

    @staticmethod
    def _select_active_swap_label(active_swaps: List[SwapView]) -> Optional[str]:
        if not active_swaps:
            return None
        if any(swap.purpose == SwapPurpose.STOPLOSS for swap in active_swaps):
            return SwapPurpose.STOPLOSS.value
        if any(swap.purpose == SwapPurpose.INVENTORY for swap in active_swaps):
            return SwapPurpose.INVENTORY.value
        return active_swaps[0].level_id

    def _rule_manual_kill_switch(
        self,
        snapshot: Snapshot,
        _: ControllerContext,
        regions: Regions,
    ) -> Optional[Decision]:
        if not regions.manual_stop:
            return None
        actions = [StopExecutorAction(controller_id=self.config.id, executor_id=v.executor_id) for v in snapshot.active_lp]
        patch = DecisionPatch()
        if actions:
            patch.swap.awaiting_balance_refresh = True
            patch.swap.awaiting_balance_refresh_since = snapshot.now
        return Decision(
            intent=Intent(flow=IntentFlow.MANUAL, stage=IntentStage.STOP_LP, reason="manual_kill_switch"),
            actions=actions,
            patch=patch,
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
        actions: List[ExecutorAction] = []
        failed_view = snapshot.lp.get(failed_id)
        if failed_view is not None and failed_view.is_active:
            actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=failed_id))
        patch = DecisionPatch()
        patch.failure.set_reason = reason
        patch.rebalance.clear_all = True
        patch.stoploss.pending_liquidation = False
        if actions:
            patch.swap.awaiting_balance_refresh = True
            patch.swap.awaiting_balance_refresh_since = snapshot.now
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
            return Decision(intent=Intent(flow=IntentFlow.NONE, stage=IntentStage.NONE, reason="idle"))
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
        patch.stoploss.until_ts = snapshot.now + self.config.stop_loss_pause_sec
        if actions:
            patch.swap.awaiting_balance_refresh = True
            patch.swap.awaiting_balance_refresh_since = snapshot.now
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

    def _decide_liquidation(self, snapshot: Snapshot, ctx: ControllerContext) -> Decision:
        now = snapshot.now
        if ctx.stoploss.last_liquidation_attempt_ts > 0 and self.config.cooldown_seconds > 0:
            if (now - ctx.stoploss.last_liquidation_attempt_ts) < self.config.cooldown_seconds:
                return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="liquidation_cooldown"))

        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return Decision(intent=Intent(flow=IntentFlow.STOPLOSS, stage=IntentStage.WAIT, reason="price_unavailable"))

        base_to_liquidate = snapshot.wallet_base
        target = ctx.stoploss.liquidation_target_base
        if target is not None:
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
            expected_base = max(Decimal("0"), target or Decimal("0"))
            if snapshot.wallet_base <= 0 and expected_base > 0:
                patch = DecisionPatch()
                patch.swap.awaiting_balance_refresh = True
                patch.swap.awaiting_balance_refresh_since = now
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
            build_open_lp_action=self._build_open_lp_action,
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
        range_plan = self._build_range_plan(current_price)
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

        if self.config.cooldown_seconds > 0 and (now - ctx.swap.last_inventory_swap_ts) < self.config.cooldown_seconds:
            return Decision(intent=Intent(flow=flow, stage=IntentStage.WAIT, reason="swap_cooldown"))

        swap_action = self._build_inventory_swap_action(current_price, delta_base)
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

    def _min_liquidation_base(self, current_price: Decimal) -> Decimal:
        if current_price <= 0:
            return Decimal("0")
        min_quote = self._swap_min_quote_value()
        min_base = min_quote / current_price if min_quote > 0 else Decimal("0")
        return max(min_base, Decimal("0.00000001"))

    def _build_swap_action(
        self,
        *,
        level_id: str,
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
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.router_connector,
            trading_pair=self.config.trading_pair,
            side=side,
            amount=amount,
            amount_in_is_quote=amount_in_is_quote,
            slippage_pct=self._swap_slippage_pct(),
            pool_address=self.config.pool_address or None,
            level_id=level_id,
            budget_key=self._budget_key,
        )
        return CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=executor_config,
        )

    def _build_inventory_swap_action(
        self,
        current_price: Optional[Decimal],
        delta_base: Decimal,
    ) -> Optional[CreateExecutorAction]:
        if current_price is None or current_price <= 0:
            return None

        if delta_base > 0:
            amount = abs(delta_base * current_price)
            return self._build_swap_action(
                level_id="inventory",
                side=TradeType.BUY,
                amount=amount,
                amount_in_is_quote=True,
                apply_buffer=False,
            )
        if delta_base < 0:
            amount = abs(delta_base)
            return self._build_swap_action(
                level_id="inventory",
                side=TradeType.SELL,
                amount=amount,
                amount_in_is_quote=False,
                apply_buffer=True,
            )
        return None

    def _build_liquidation_action(self, base_amount: Decimal) -> Optional[CreateExecutorAction]:
        if base_amount <= 0:
            return None
        return self._build_swap_action(
            level_id="liquidate",
            side=TradeType.SELL,
            amount=base_amount,
            amount_in_is_quote=False,
            apply_buffer=False,
        )

    def _apply_swap_buffer(self, amount: Decimal) -> Decimal:
        buffer_pct = max(Decimal("0"), self.config.swap_safety_buffer_pct)
        if buffer_pct <= 0:
            return amount
        if buffer_pct >= 1:
            return Decimal("0")
        return amount * (Decimal("1") - buffer_pct)

    def _swap_slippage_pct(self) -> Decimal:
        return max(Decimal("0"), self.config.swap_slippage_pct) * Decimal("100")

    def _build_open_lp_action(self, proposal: OpenProposal) -> Optional[CreateExecutorAction]:
        executor_config = self._create_lp_executor_config(proposal)
        if executor_config is None:
            return None
        return CreateExecutorAction(controller_id=self.config.id, executor_config=executor_config)

    def _create_lp_executor_config(self, proposal: OpenProposal) -> Optional[LPPositionExecutorConfig]:
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
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            pool_address=self.config.pool_address,
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
        extra_params = self._policy.extra_lp_params()
        if extra_params:
            executor_config.extra_params = extra_params
        reservation_id = self._reserve_budget(proposal.open_base, proposal.open_quote)
        if reservation_id is None:
            return None
        executor_config.budget_reservation_id = reservation_id
        return executor_config

    def _build_range_plan(self, center_price: Optional[Decimal]):
        if center_price is None or center_price <= 0:
            return None
        return self._policy.range_plan(center_price)

    def _reconcile(self, snapshot: Snapshot) -> None:
        self._clear_stale_balance_refresh(snapshot.now)
        self._reconcile_done_swaps(snapshot)
        self._rebalance_engine.reconcile(snapshot, self._ctx)
        self._ensure_anchors(snapshot)
        self._update_fee_rate_estimates(snapshot)
        self._cleanup_ctx(snapshot)

    def _cleanup_ctx(self, snapshot: Snapshot) -> None:
        for executor_id in list(self._ctx.lp.keys()):
            lp_view = snapshot.lp.get(executor_id)
            if lp_view is None or lp_view.is_done:
                self._ctx.lp.pop(executor_id, None)
        if self._ctx.swap.settled_executor_ids:
            active_swap_ids = set(snapshot.swaps.keys())
            self._ctx.swap.settled_executor_ids.intersection_update(active_swap_ids)

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
            requirements[self._domain.base_token] = base_amt
        if quote_amt > 0:
            requirements[self._domain.quote_token] = quote_amt
        reservation_id = self._budget_coordinator.reserve(
            connector_name=self.config.connector_name,
            connector=connector,
            requirements=requirements,
            native_token=self.config.native_token_symbol,
            min_native_balance=self.config.min_native_balance,
        )
        return reservation_id

    def _reconcile_done_swaps(self, snapshot: Snapshot):
        now = snapshot.now
        for swap in snapshot.swaps.values():
            if not swap.is_done:
                continue
            if swap.executor_id in self._ctx.swap.settled_executor_ids:
                continue
            self._ctx.swap.settled_executor_ids.add(swap.executor_id)

            if swap.purpose == SwapPurpose.STOPLOSS:
                self._handle_liquidation_swap(swap, now)
            elif swap.purpose == SwapPurpose.INVENTORY:
                self._handle_inventory_swap(swap, now)

    @staticmethod
    def _swap_purpose(level_id: Optional[str]) -> Optional[SwapPurpose]:
        if level_id == SwapPurpose.INVENTORY.value:
            return SwapPurpose.INVENTORY
        if level_id == SwapPurpose.STOPLOSS.value:
            return SwapPurpose.STOPLOSS
        return None

    def _handle_inventory_swap(self, swap: SwapView, now: float) -> None:
        if swap.close_type != CloseType.COMPLETED:
            return
        self._mark_balance_refresh(now)

    def _handle_liquidation_swap(self, swap: SwapView, now: float) -> None:
        self._ctx.stoploss.last_liquidation_attempt_ts = now
        if swap.close_type != CloseType.COMPLETED:
            self._ctx.stoploss.pending_liquidation = True
            return
        self._mark_balance_refresh(now)
        target = self._ctx.stoploss.liquidation_target_base
        if target is None:
            self._ctx.stoploss.pending_liquidation = True
            return
        sold = self._resolve_liquidation_sold_base(swap)
        remaining = max(Decimal("0"), target - sold)
        if remaining <= 0:
            self._ctx.stoploss.pending_liquidation = False
            self._ctx.stoploss.liquidation_target_base = None
            return
        self._ctx.stoploss.pending_liquidation = True
        self._ctx.stoploss.liquidation_target_base = remaining

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

    def _mark_balance_refresh(self, now: float) -> None:
        self._ctx.swap.awaiting_balance_refresh = True
        self._ctx.swap.awaiting_balance_refresh_since = now
