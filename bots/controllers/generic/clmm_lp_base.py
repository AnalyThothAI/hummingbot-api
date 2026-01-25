import asyncio
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from pydantic import Field

from hummingbot.core.data_type.common import MarketDict
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.budget.budget_coordinator import BudgetCoordinatorRegistry
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction

from .clmm_lp_domain.components import ControllerContext, LPView, OpenProposal, Snapshot, PoolDomainAdapter
from .clmm_lp_domain.cost_filter import CostFilter
from .clmm_lp_domain.clmm_fsm import CLMMFSM
from .clmm_lp_domain.policies import CLMMPolicyBase
from .clmm_lp_domain.rebalance_engine import RebalanceEngine
from .clmm_lp_domain.exit_policy import ExitPolicy
from .clmm_lp_domain.io import ActionFactory, BalanceManager, SnapshotBuilder


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
    take_profit_pnl_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    stop_loss_pause_sec: int = Field(default=1800, json_schema_extra={"is_updatable": True})
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

        self._ctx = ControllerContext()
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
            config=self.config,
            domain=self._domain,
            market_data_provider=self.market_data_provider,
        )
        self._rebalance_engine = RebalanceEngine(
            config=self.config,
            estimate_position_value=self._estimate_position_value,
        )
        self._exit_policy = ExitPolicy(config=self.config)
        self._fsm = CLMMFSM(
            config=self.config,
            action_factory=self._action_factory,
            build_open_proposal=self._build_open_proposal,
            estimate_position_value=self._estimate_position_value,
            rebalance_engine=self._rebalance_engine,
            exit_policy=self._exit_policy,
        )
        self._latest_snapshot: Optional[Snapshot] = None

        self._last_policy_update_ts: float = 0.0
        self._policy_update_interval_sec: float = 600.0
        self._policy_bootstrap_interval_sec: float = 30.0
        self._policy_update_timeout_sec: float = 10.0
        self._policy_update_task: Optional[asyncio.Task] = None

        rate_connector = self.config.router_connector
        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=rate_connector,
                trading_pair=self.config.trading_pair,
            ),
        ])

    async def update_processed_data(self):
        now = self.market_data_provider.time()
        self._balance_manager.schedule_refresh(now)
        snapshot = self._build_snapshot(now)
        self._latest_snapshot = snapshot
        self._update_fee_rate_estimates(snapshot)

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

    def determine_executor_actions(self) -> List[ExecutorAction]:
        snapshot = self._latest_snapshot
        if snapshot is None:
            snapshot = self._build_snapshot(self.market_data_provider.time())
        self._latest_snapshot = None
        balance_fresh = self._balance_manager.is_fresh(snapshot.now)
        decision = self._fsm.step(snapshot, self._ctx, balance_fresh)
        self._log_decision_actions(decision)
        return decision.actions

    def get_custom_info(self) -> Dict:
        snapshot = self._latest_snapshot or self._build_snapshot(self.market_data_provider.time())
        nav = self._compute_nav(snapshot)
        if nav is None:
            return {}
        nav_value, lp_value, wallet_value, budget_value = nav
        current_price = snapshot.current_price
        now = snapshot.now

        def _as_float(value: Optional[Decimal]) -> Optional[float]:
            return float(value) if value is not None else None

        info = {
            "state": self._ctx.state.value,
            "state_since": self._ctx.state_since_ts,
            "last_reason": self._ctx.last_decision_reason,
            "nav_quote": float(nav_value),
            "nav_lp_quote": float(lp_value),
            "nav_wallet_quote": float(wallet_value),
            "wallet_base": float(snapshot.wallet_base),
            "wallet_quote": float(snapshot.wallet_quote),
            "active_lp_count": len(snapshot.active_lp),
            "active_swap_count": len(snapshot.active_swaps),
            "balance_fresh": self._balance_manager.is_fresh(now),
        }
        if current_price is not None:
            info["price"] = float(current_price)
        if budget_value > 0:
            info["nav_budget_quote"] = float(budget_value)
        if self._ctx.anchor_value_quote is not None:
            info["anchor_value_quote"] = float(self._ctx.anchor_value_quote)

        cooldown_remaining = max(0.0, self._ctx.cooldown_until_ts - now)
        if cooldown_remaining > 0:
            info["cooldown_remaining_sec"] = cooldown_remaining

        if snapshot.active_lp:
            lp_view = snapshot.active_lp[0]
            in_range = None
            position_value = None
            if current_price is not None:
                position_value = self._estimate_position_value(lp_view, current_price)
                if lp_view.lower_price is not None and lp_view.upper_price is not None:
                    in_range = lp_view.lower_price <= current_price <= lp_view.upper_price
            info["active_lp"] = {
                "executor_id": lp_view.executor_id,
                "lower_price": _as_float(lp_view.lower_price),
                "upper_price": _as_float(lp_view.upper_price),
                "in_range": in_range,
                "position_value_quote": _as_float(position_value),
                "base": _as_float(abs(lp_view.base_amount)),
                "quote": _as_float(abs(lp_view.quote_amount)),
                "out_of_range_since": lp_view.out_of_range_since,
            }
        return info

    def _log_decision_actions(self, decision) -> None:
        if not decision.actions:
            return
        self.logger().info(
            "Decision %s/%s | actions=%s",
            self._ctx.state.value,
            decision.reason or "",
            len(decision.actions),
        )

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

    def _estimate_position_value(self, lp_view: LPView, current_price: Decimal) -> Decimal:
        base_amount = abs(lp_view.base_amount)
        quote_amount = abs(lp_view.quote_amount)
        base_fee = abs(lp_view.base_fee)
        quote_fee = abs(lp_view.quote_fee)
        return (base_amount + base_fee) * current_price + (quote_amount + quote_fee)

    def _update_fee_rate_estimates(self, snapshot: Snapshot) -> None:
        current_price = snapshot.current_price
        if current_price is None or current_price <= 0:
            return
        if not snapshot.active_lp:
            return
        lp_view = snapshot.active_lp[0]
        if lp_view.state != LPPositionStates.IN_RANGE.value:
            return
        position_address = lp_view.position_address or ""
        if not position_address:
            return
        CostFilter.update_fee_rate_ewma(
            now=snapshot.now,
            position_address=position_address,
            base_fee=lp_view.base_fee,
            quote_fee=lp_view.quote_fee,
            price=current_price,
            ctx=self._ctx.fee,
        )

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

        targets = self._policy.target_amounts_from_value(effective_budget, current_price, ratio)
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
