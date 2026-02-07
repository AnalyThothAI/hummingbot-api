import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ConfigDict, Field

from hummingbot.core.data_type.common import MarketDict
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.budget.budget_coordinator import BudgetCoordinatorRegistry
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from .clmm_lp_domain.components import (
    ControllerContext,
    ControllerState,
    LPView,
    OpenProposal,
    PoolDomainAdapter,
    PriceContext,
    Snapshot,
    pct_to_ratio,
)
from .clmm_lp_domain.clmm_fsm import CLMMFSM
from .clmm_lp_domain.policies import CLMMPolicyBase
from .clmm_lp_domain.rebalance_engine import RebalanceEngine
from .clmm_lp_domain.exit_policy import ExitPolicy
from .clmm_lp_domain.io import ActionFactory, BalanceManager, SnapshotBuilder, PriceProvider


class CLMMLPBaseConfig(ControllerConfigBase):
    model_config = ConfigDict(extra="ignore")
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
    rebalance_enabled: bool = Field(default=False, json_schema_extra={"is_updatable": True})
    rebalance_seconds: int = Field(default=60, json_schema_extra={"is_updatable": True})
    hysteresis_pct: Decimal = Field(default=Decimal("0.002"), json_schema_extra={"is_updatable": True})
    cooldown_seconds: int = Field(default=30, json_schema_extra={"is_updatable": True})
    max_rebalances_per_hour: int = Field(default=20, json_schema_extra={"is_updatable": True})
    rebalance_open_timeout_sec: int = Field(default=300, json_schema_extra={"is_updatable": True})

    exit_full_liquidation: bool = Field(default=False, json_schema_extra={"is_updatable": True})
    exit_swap_slippage_pct: Decimal = Field(default=Decimal("0.01"), json_schema_extra={"is_updatable": True})
    max_exit_swap_attempts: int = Field(default=5, json_schema_extra={"is_updatable": True})

    stop_loss_pnl_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    take_profit_pnl_pct: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    stop_loss_pause_sec: int = Field(default=1800, json_schema_extra={"is_updatable": True})
    reenter_enabled: bool = Field(default=False, json_schema_extra={"is_updatable": True})

    budget_key: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    native_token_symbol: Optional[str] = Field(default=None, json_schema_extra={"is_updatable": True})
    min_native_balance: Decimal = Field(default=Decimal("0"), json_schema_extra={"is_updatable": True})
    balance_update_timeout_sec: int = Field(default=10, json_schema_extra={"is_updatable": True})
    balance_refresh_timeout_sec: int = Field(default=30, json_schema_extra={"is_updatable": True})

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pool_pair = self.pool_trading_pair or self.trading_pair
        markets = markets.add_or_update(self.connector_name, pool_pair)
        if self.router_connector:
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
        self._rate_connector = self.config.router_connector or self.config.connector_name
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
            logger=self.logger,
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
        self._price_provider = PriceProvider(
            connector_name=self._rate_connector,
            trading_pair=self.config.trading_pair,
            market_data_provider=self.market_data_provider,
            logger=self.logger,
        )
        self._latest_snapshot: Optional[Snapshot] = None
        self._latest_price_context: Optional[PriceContext] = None
        self._last_lp_position: Dict[str, Optional[str]] = {}
        self._last_tick_log_ts: float = 0.0

        self._last_policy_update_ts: float = 0.0
        self._policy_update_interval_sec: float = 600.0
        self._policy_bootstrap_interval_sec: float = 30.0
        self._policy_update_timeout_sec: float = 10.0
        self._policy_update_task: Optional[asyncio.Task] = None

        rate_connector = self._rate_connector
        # LPPositionExecutor reads price via RateOracle using its own config.trading_pair, which is
        # the pool trading pair (token0-token1 order). If pool_trading_pair is inverted vs the
        # strategy trading_pair, RateOracle can often infer the inverse, but that depends on token
        # symbol normalization. Register both to avoid the executor being stuck in OPENING due to
        # missing rates on some connectors.
        pool_pair = self.config.pool_trading_pair or self.config.trading_pair
        rate_pairs = [
            ConnectorPair(connector_name=rate_connector, trading_pair=self.config.trading_pair),
        ]
        if pool_pair and pool_pair != self.config.trading_pair:
            rate_pairs.append(ConnectorPair(connector_name=rate_connector, trading_pair=pool_pair))
        self.market_data_provider.initialize_rate_sources(rate_pairs)

    async def update_processed_data(self):
        now = self.market_data_provider.time()
        # 心跳与本 tick 的统一时间源。
        self._ctx.last_tick_ts = now
        # 监听 LP 开/关仓变化，用于触发余额刷新事件。
        self._detect_lp_position_changes(now)
        # 事件驱动的余额刷新调度，避免阻塞 tick。
        force_balance = self._ctx.force_balance_refresh_until_ts > now
        self._balance_manager.schedule_refresh(now, force=force_balance)
        # 一旦余额快照刷新完成，清理强制刷新标记。
        if force_balance and self._balance_manager.is_fresh(now):
            self._ctx.force_balance_refresh_until_ts = 0.0
            self._ctx.force_balance_refresh_reason = None
        # 构建本 tick 的一致性快照。
        snapshot = self._refresh_snapshot(now)
        self._latest_snapshot = snapshot
        # 已移除 cost filter，避免在 tick 中做额外估计。

        # 周期性刷新 policy 输入（如 tick spacing），避免阻塞。
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
            snapshot = self._refresh_snapshot(self.market_data_provider.time())
        self._latest_snapshot = None
        decision = self._fsm.step(snapshot, self._ctx)
        self._log_decision_actions(decision)
        self._log_decision_metrics(decision, snapshot)
        return decision.actions

    def get_custom_info(self) -> Dict:
        snapshot = self._latest_snapshot or self._refresh_snapshot(self.market_data_provider.time())
        now = snapshot.now
        has_active_lp = bool(snapshot.active_lp)

        def _as_float(value: Optional[Decimal]) -> Optional[float]:
            return float(value) if value is not None else None

        price_ctx = self._latest_price_context
        price = snapshot.current_price
        price_source = price_ctx.source if price_ctx is not None else "snapshot"
        price_ts = price_ctx.timestamp if price_ctx is not None and price_ctx.timestamp > 0 else None
        price_age = (now - price_ts) if price_ts is not None else None
        (
            risk_cap_quote,
            risk_equity_quote,
            _risk_wallet_contrib,
            lp_value_quote,
            wallet_value_quote,
        ) = self._compute_risk_values(snapshot, price)

        anchor = self._ctx.anchor_value_quote
        stoploss_trigger_quote = None
        stop_loss_ratio = pct_to_ratio(self.config.stop_loss_pnl_pct)
        if anchor is not None and anchor > 0 and stop_loss_ratio > 0:
            stoploss_trigger_quote = anchor * (Decimal("1") - stop_loss_ratio)
        take_profit_trigger_quote = None
        take_profit_ratio = pct_to_ratio(self.config.take_profit_pnl_pct)
        if anchor is not None and anchor > 0 and take_profit_ratio > 0:
            take_profit_trigger_quote = anchor * (Decimal("1") + take_profit_ratio)

        realized_pnl_quote = self._ctx.realized_pnl_quote
        unrealized_pnl_quote: Optional[Decimal] = None
        net_pnl_quote: Optional[Decimal] = None
        net_pnl_pct: Optional[Decimal] = None
        if anchor is not None and anchor > 0:
            # "lp_only" PnL semantics:
            # - When LP is active: unrealized is computed from LP equity.
            # - When no LP is active: unrealized is 0 and net == realized, to avoid confusing
            #   negative unrealized displays during IDLE/EXIT phases.
            if has_active_lp and risk_equity_quote is not None:
                unrealized_pnl_quote = risk_equity_quote - anchor
                net_pnl_quote = realized_pnl_quote + unrealized_pnl_quote
            else:
                unrealized_pnl_quote = Decimal("0")
                net_pnl_quote = realized_pnl_quote
            net_pnl_pct = net_pnl_quote / anchor

        rebalance_count_1h = 0
        if self._ctx.rebalance_timestamps:
            rebalance_count_1h = sum(1 for ts in self._ctx.rebalance_timestamps if (now - ts) <= 3600)
        cooldown_remaining = max(0.0, self._ctx.cooldown_until_ts - now)

        positions: List[Dict[str, Any]] = []
        for lp_view in snapshot.active_lp:
            position_value = None
            if price is not None and price > 0:
                position_value = self._estimate_position_value(lp_view, price)
            positions.append({
                "executor_id": lp_view.executor_id,
                "state": lp_view.state,
                "position_address": lp_view.position_address,
                "lower_price": _as_float(lp_view.lower_price),
                "upper_price": _as_float(lp_view.upper_price),
                "position_value_quote": _as_float(position_value),
                "base_amount": _as_float(abs(lp_view.base_amount)),
                "quote_amount": _as_float(abs(lp_view.quote_amount)),
                "base_fee": _as_float(abs(lp_view.base_fee)),
                "quote_fee": _as_float(abs(lp_view.quote_fee)),
                "out_of_range_since": lp_view.out_of_range_since,
            })

        swaps: List[Dict[str, Any]] = []
        for swap_view in snapshot.active_swaps:
            swaps.append({
                "executor_id": swap_view.executor_id,
                "purpose": swap_view.purpose.value if swap_view.purpose else None,
                "amount": _as_float(abs(swap_view.amount)),
                "is_done": swap_view.is_done,
            })

        diagnostics: Dict[str, Any] = {
            "balance_fresh": self._balance_manager.is_fresh(now),
            "domain_ready": self._ctx.domain_ready,
            "domain_error": self._ctx.domain_error,
            "domain_resolved_ts": self._ctx.domain_resolved_ts if self._ctx.domain_resolved_ts > 0 else None,
        }

        heartbeat_ts = self._ctx.last_tick_ts if self._ctx.last_tick_ts > 0 else None
        heartbeat_age = None
        if heartbeat_ts is not None:
            heartbeat_age = max(0.0, now - heartbeat_ts)

        info: Dict[str, Any] = {
            "state": {
                "value": self._ctx.state.value,
                "since": self._ctx.state_since_ts,
                "reason": self._ctx.last_decision_reason,
            },
            "heartbeat": {
                "last_tick_ts": heartbeat_ts,
                "tick_age_sec": heartbeat_age,
            },
            "price": {
                "value": _as_float(price),
                "source": price_source,
                "timestamp": price_ts,
                "age_sec": float(price_age) if price_age is not None else None,
            },
            # Token/unit helpers for dashboards and external consumers.
            # This allows UI to label values correctly even when it cannot load controller YAML configs.
            "pair": {
                "trading_pair": self._domain.trading_pair,
                "pool_trading_pair": self._domain.pool_trading_pair,
                "base_symbol": self._domain.base_token,
                "quote_symbol": self._domain.quote_token,
                "pool_base_symbol": self._domain.pool_base_token,
                "pool_quote_symbol": self._domain.pool_quote_token,
                "pool_order_inverted": bool(self._domain.pool_order_inverted),
            },
            "wallet": {
                "base": _as_float(snapshot.wallet_base),
                "quote": _as_float(snapshot.wallet_quote),
                "value_quote": _as_float(wallet_value_quote),
                "source": self._balance_manager.wallet_source,
            },
            "lp": {
                "active_count": len(snapshot.active_lp),
                "value_quote": _as_float(lp_value_quote),
                "positions": positions,
            },
            "swaps": {
                "active_count": len(snapshot.active_swaps),
                "active": swaps,
            },
            "risk": {
                "budget_mode": "lp_only",
                "cap_quote": _as_float(risk_cap_quote),
                "equity_quote": _as_float(risk_equity_quote),
                "anchor_quote": _as_float(anchor),
                "stoploss_trigger_quote": _as_float(stoploss_trigger_quote),
                "take_profit_trigger_quote": _as_float(take_profit_trigger_quote),
                "pnl_realized_quote": _as_float(realized_pnl_quote),
                "pnl_unrealized_quote": _as_float(unrealized_pnl_quote),
                "pnl_net_quote": _as_float(net_pnl_quote),
                "pnl_net_pct": _as_float(net_pnl_pct),
                "exit_full_liquidation": bool(self.config.exit_full_liquidation),
            },
            "rebalance": {
                "out_of_range_since": self._ctx.out_of_range_since,
                "cooldown_remaining_sec": cooldown_remaining,
                "count_1h": rebalance_count_1h,
                "count_total": int(self._ctx.rebalance_count),
                "last_ts": self._ctx.last_rebalance_ts if self._ctx.last_rebalance_ts > 0 else None,
                "signal_reason": self._ctx.rebalance_signal_reason,
            },
            "diagnostics": diagnostics,
        }
        return info

    def _resolve_price_context(self, now: float) -> PriceContext:
        return self._price_provider.get_price_context(now)

    def _compute_risk_values(
        self,
        snapshot: Snapshot,
        price: Optional[Decimal],
    ) -> Tuple[Decimal, Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        risk_cap_quote = max(Decimal("0"), self.config.position_value_quote)
        wallet_value_quote: Optional[Decimal] = None
        lp_value_quote: Optional[Decimal] = None
        if price is not None and price > 0:
            wallet_value_quote = snapshot.wallet_base * price + snapshot.wallet_quote
            if snapshot.active_lp:
                lp_value_quote = sum(self._estimate_position_value(lp_view, price) for lp_view in snapshot.active_lp)
            else:
                lp_value_quote = Decimal("0")

        risk_equity_quote: Optional[Decimal] = None
        if lp_value_quote is not None:
            risk_equity_quote = lp_value_quote
        return risk_cap_quote, risk_equity_quote, None, lp_value_quote, wallet_value_quote

    def _log_decision_metrics(self, decision, snapshot: Snapshot) -> None:
        price = snapshot.current_price
        price_source = self._latest_price_context.source if self._latest_price_context is not None else "snapshot"
        now = snapshot.now
        if (now - self._last_tick_log_ts) >= 10:
            self._last_tick_log_ts = now
            self._log_metric_event(
                "tick",
                state=self._ctx.state.value,
                price=price,
                source=price_source,
                wallet_base=snapshot.wallet_base,
                wallet_quote=snapshot.wallet_quote,
            )
        if not decision.reason:
            return

        if decision.reason.startswith("stop_loss"):
            (
                _risk_cap,
                risk_equity,
                _risk_wallet_contrib,
                lp_value,
                _wallet_value,
            ) = self._compute_risk_values(snapshot, price)
            anchor = self._ctx.anchor_value_quote
            trigger = None
            stop_loss_ratio = pct_to_ratio(self.config.stop_loss_pnl_pct)
            if anchor is not None and anchor > 0 and stop_loss_ratio > 0:
                trigger = anchor * (Decimal("1") - stop_loss_ratio)
            self._log_metric_event(
                "stoploss_trigger",
                price=price,
                source=price_source,
                anchor=anchor,
                equity=risk_equity,
                trigger=trigger,
                wallet_base=snapshot.wallet_base,
                wallet_quote=snapshot.wallet_quote,
                lp_value=lp_value,
            )
            return

        if decision.reason == "out_of_range_rebalance":
            lp_view = min(snapshot.active_lp, key=lambda lp: lp.executor_id) if snapshot.active_lp else None
            lower = lp_view.lower_price if lp_view else None
            upper = lp_view.upper_price if lp_view else None
            deviation_pct = None
            if price is not None and lower is not None and upper is not None and lower > 0 and upper > 0:
                if price < lower:
                    deviation_pct = (lower - price) / lower * Decimal("100")
                elif price > upper:
                    deviation_pct = (price - upper) / upper * Decimal("100")
                else:
                    deviation_pct = Decimal("0")
            self._log_metric_event(
                "rebalance_trigger",
                price=price,
                source=price_source,
                lower=lower,
                upper=upper,
                deviation_pct=deviation_pct,
                out_of_range_since=self._ctx.out_of_range_since,
                rebalance_seconds=self.config.rebalance_seconds,
                hysteresis_pct=self.config.hysteresis_pct,
                cooldown_seconds=self.config.cooldown_seconds,
            )

    def _log_metric_event(self, event: str, **fields: Any) -> None:
        parts: List[str] = []
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        payload = " ".join(parts)
        if payload:
            self.logger().info("metric_%s | %s", event, payload)

    def _log_decision_actions(self, decision) -> None:
        if not decision.actions:
            return
        self.logger().info(
            "Decision %s/%s | actions=%s",
            self._ctx.state.value,
            decision.reason or "",
            len(decision.actions),
        )

    def _build_snapshot(
        self,
        now: float,
        *,
        wallet_base: Decimal,
        wallet_quote: Decimal,
        balance_fresh: bool,
        current_price: Optional[Decimal],
    ) -> Snapshot:
        return self._snapshot_builder.build(
            now=now,
            current_price=current_price,
            executors_info=self.executors_info,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            balance_fresh=balance_fresh,
        )

    def _refresh_snapshot(self, now: float) -> Snapshot:
        force_balance = self._ctx.force_balance_refresh_until_ts > now
        self._balance_manager.schedule_refresh(now, force=force_balance)
        price_ctx = self._resolve_price_context(now)
        self._latest_price_context = price_ctx
        balance_fresh = self._balance_manager.is_fresh(now)
        raw_snapshot = self._build_snapshot(
            now,
            wallet_base=self._balance_manager.wallet_base,
            wallet_quote=self._balance_manager.wallet_quote,
            balance_fresh=balance_fresh,
            current_price=price_ctx.value,
        )
        return raw_snapshot

    def _detect_lp_position_changes(self, now: float) -> None:
        current_lp_ids = set()
        for executor in self.executors_info:
            if executor.controller_id != self.config.id:
                continue
            if executor.type != "lp_position_executor":
                continue
            current_lp_ids.add(executor.id)
            custom = executor.custom_info or {}
            position_address = None
            if isinstance(custom, dict):
                position_address = custom.get("position_address")
            prev_address = self._last_lp_position.get(executor.id)
            if prev_address != position_address:
                if prev_address is None and position_address:
                    self._request_force_balance_refresh(now, "lp_open")
                elif prev_address and position_address is None:
                    self._request_force_balance_refresh(now, "lp_close")
            self._last_lp_position[executor.id] = position_address
        for executor_id in list(self._last_lp_position.keys()):
            if executor_id not in current_lp_ids:
                self._last_lp_position.pop(executor_id, None)

    def _request_force_balance_refresh(self, now: float, reason: str) -> None:
        ttl = max(2, int(self.config.balance_update_timeout_sec))
        deadline = now + ttl
        if deadline > self._ctx.force_balance_refresh_until_ts:
            self._ctx.force_balance_refresh_until_ts = deadline
            self._ctx.force_balance_refresh_reason = reason

    def _estimate_position_value(self, lp_view: LPView, current_price: Decimal) -> Decimal:
        base_amount = abs(lp_view.base_amount)
        quote_amount = abs(lp_view.quote_amount)
        base_fee = abs(lp_view.base_fee)
        quote_fee = abs(lp_view.quote_fee)
        return (base_amount + base_fee) * current_price + (quote_amount + quote_fee)

    def _build_open_proposal(
        self,
        current_price: Optional[Decimal],
        wallet_base: Decimal,
        wallet_quote: Decimal,
        anchor_value_quote: Optional[Decimal],
    ) -> Tuple[Optional[OpenProposal], Optional[str]]:
        if current_price is None or current_price <= 0:
            return None, "price_unavailable"
        total_value = anchor_value_quote
        if total_value is None or total_value <= 0:
            total_value = self.config.position_value_quote
        total_value = max(Decimal("0"), total_value)
        if total_value <= 0:
            return None, "budget_unavailable"
        total_wallet_value = wallet_base * current_price + wallet_quote
        effective_budget = min(total_value, total_wallet_value)
        if effective_budget <= 0:
            return None, "insufficient_balance"
        # Avoid "dust both-sides" opens: if one side is only a tiny fraction of the intended budget,
        # treat it as absent so we can open a single-sided range plan instead of minting dust liquidity.
        min_side_value_quote = effective_budget * Decimal("0.01")  # 1% of effective budget
        base_value_quote = wallet_base * current_price
        has_base = base_value_quote >= min_side_value_quote
        has_quote = wallet_quote >= min_side_value_quote
        if has_base and has_quote:
            side = "both"
        elif has_base:
            side = "base"
        elif has_quote:
            side = "quote"
        else:
            return None, "insufficient_balance"

        range_plan = self._policy.range_plan_for_side(current_price, side)
        if range_plan is None:
            return None, "range_unavailable"

        target_base = Decimal("0")
        target_quote = Decimal("0")
        open_base = Decimal("0")
        open_quote = Decimal("0")

        if side == "both":
            ratio = self._policy.quote_per_base_ratio(current_price, range_plan.lower, range_plan.upper)
            if ratio is None:
                return None, "ratio_unavailable"
            targets = self._policy.target_amounts_from_value(effective_budget, current_price, ratio)
            if targets is None:
                return None, "target_unavailable"
            target_base, target_quote = targets
            open_base = min(wallet_base, target_base)
            open_quote = min(wallet_quote, target_quote)
        elif side == "base":
            target_base = min(wallet_base, effective_budget / current_price)
            open_base = target_base
        else:
            target_quote = min(wallet_quote, effective_budget)
            open_quote = target_quote

        if open_base <= 0 and open_quote <= 0:
            return None, "insufficient_balance"

        return OpenProposal(
            lower=range_plan.lower,
            upper=range_plan.upper,
            open_base=open_base,
            open_quote=open_quote,
            target_base=target_base,
            target_quote=target_quote,
        ), None

    def _clear_policy_update_task(self, task: asyncio.Task) -> None:
        if self._policy_update_task is task:
            self._policy_update_task = None

    def on_stop(self):
        self._price_provider.stop()

    async def _safe_policy_update(self, connector) -> None:
        try:
            await asyncio.wait_for(self._policy.update(connector), timeout=self._policy_update_timeout_sec)
        except Exception:
            self.logger().exception(
                "policy.update failed | connector=%s",
                self.config.connector_name,
            )
