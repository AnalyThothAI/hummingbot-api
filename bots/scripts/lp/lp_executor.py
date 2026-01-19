"""
LP 执行器：将 CLMM LP 的预算、开关仓、再平衡与止损统一为标准 Executor 生命周期。
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Deque, Dict, Optional, Literal

from pydantic import ConfigDict, Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.gateway.common_types import ConnectorType, get_connector_type
from hummingbot.connector.gateway.gateway_lp import CLMMPoolInfo, CLMMPositionInfo
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.core.event.events import MarketOrderFailureEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.strategy_v2.executors.data_types import ExecutorConfigBase
from hummingbot.strategy_v2.executors.executor_base import ExecutorBase
from hummingbot.strategy_v2.models.executors import CloseType

from .budget_manager import BudgetManager, BudgetPlan, BudgetSettings
from .lp_position_manager import LPPositionManager, PendingOperation, PendingResult, PendingStatus
from .pool_info_feed import PoolInfoFeed, PoolInfoFeedSettings
from .range_engine import RangeEngine
from .status_formatter import format_lp_status_lines
from .swap_manager import RouterSwapManager


LogFunc = Callable[[int, str], None]


class ManageAction(Enum):
    """
    持仓管理动作类型（仅决策层使用）。

    说明:
        仅保留出界再平衡动作，止损由执行层负责。
    """

    NONE = "none"
    OUT_OF_RANGE_REBALANCE = "out_of_range_rebalance"


@dataclass
class ManageDecision:
    """
    监控决策结果（执行层据此触发动作）。

    Attributes:
        action: 动作类型。
        reason: 触发原因标签。
        current_price: 当前价格。
        old_lower: 原下边界价格。
        old_upper: 原上边界价格。
    """

    action: ManageAction
    reason: str
    current_price: Decimal
    old_lower: Decimal
    old_upper: Decimal


@dataclass
class RebalancePolicySettings:
    """
    决策策略配置集合。

    Attributes:
        hysteresis_pct: 出界滞后阈值百分比。
        rebalance_seconds: 出界持续秒数触发。
        cooldown_seconds: 重平衡冷却秒数。
        max_rebalances_per_hour: 每小时最大重平衡次数。
    """

    hysteresis_pct: Decimal
    rebalance_seconds: int
    cooldown_seconds: int
    max_rebalances_per_hour: int


@dataclass
class RebalancePolicyState:
    """
    决策策略运行状态。

    Attributes:
        out_of_bounds_since: 出界开始时间戳。
        last_rebalance_ts: 上次再平衡确认时间戳。
        rebalance_timestamps: 再平衡时间戳序列。
    """

    out_of_bounds_since: Optional[float] = None
    last_rebalance_ts: float = 0.0
    rebalance_timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=200))


class RebalancePolicy:
    """
    决策策略：根据价格与仓位状态生成管理动作。
    """

    def __init__(
        self,
        settings: RebalancePolicySettings,
        state: Optional[RebalancePolicyState] = None,
        log_func: Optional[LogFunc] = None,
    ) -> None:
        """
        初始化再平衡策略。

        Args:
            settings: 决策策略配置。
            state: 运行状态（可选，默认新建）。
            log_func: 日志函数（可选）。
        """
        self._settings = settings
        self._state = state or RebalancePolicyState()
        self._log = log_func or self._default_log

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数，使用模块 logger 输出。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    def reset_after_open(self, now: float, is_reopen: bool) -> None:
        """
        开仓确认后重置状态。

        Args:
            now: 当前时间戳。
            is_reopen: 是否由再平衡重开触发。
        """
        if is_reopen:
            self.record_rebalance(now)
            return
        self._state.out_of_bounds_since = None

    def reset_after_close(self) -> None:
        """
        关仓确认后清理止损与出界状态。
        """
        self._state.out_of_bounds_since = None

    def record_rebalance(self, now: float) -> None:
        """
        记录一次再平衡完成时间。

        Args:
            now: 当前时间戳。
        """
        self._state.last_rebalance_ts = now
        self._state.rebalance_timestamps.append(now)
        self._state.out_of_bounds_since = None

    def decide(
        self,
        now: float,
        current_price: Decimal,
        lower_price: Decimal,
        upper_price: Decimal,
    ) -> Optional[ManageDecision]:
        """
        根据当前行情与仓位状态做出决策。

        Args:
            now: 当前时间戳。
            current_price: 当前价格。
            lower_price: 当前仓位下边界。
            upper_price: 当前仓位上边界。

        Returns:
            管理决策；若无需动作则返回 None。
        """
        in_bounds = lower_price <= current_price <= upper_price
        if in_bounds:
            self._state.out_of_bounds_since = None
            return None

        deviation_pct = self._out_of_range_deviation_pct(current_price, lower_price, upper_price)
        if deviation_pct < self._settings.hysteresis_pct:
            return None

        if self._state.out_of_bounds_since is None:
            self._state.out_of_bounds_since = now
            side = "base" if current_price < lower_price else "quote"
            self._log(logging.INFO, f"Out of bounds start. side={side} deviation={deviation_pct:.2f}%")
            return None

        if now - self._state.out_of_bounds_since < self._settings.rebalance_seconds:
            return None
        if now - self._state.last_rebalance_ts < self._settings.cooldown_seconds:
            return None
        if not self._can_rebalance_now(now):
            self._log(logging.WARNING, "Rebalance throttled by max_rebalances_per_hour.")
            return None

        return ManageDecision(
            action=ManageAction.OUT_OF_RANGE_REBALANCE,
            reason="out_of_range",
            current_price=current_price,
            old_lower=lower_price,
            old_upper=upper_price,
        )

    def _out_of_range_deviation_pct(self, price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        """
        计算价格超出区间的偏离百分比。

        Args:
            price: 当前价格。
            lower: 下边界价格。
            upper: 上边界价格。

        Returns:
            偏离百分比。
        """
        if price < lower:
            return (lower - price) / lower * Decimal("100")
        if price > upper:
            return (price - upper) / upper * Decimal("100")
        return Decimal("0")

    def snapshot(self, now: float) -> dict:
        """
        获取再平衡状态快照（仅用于观测）。

        Args:
            now: 当前时间戳。

        Returns:
            再平衡状态信息字典。
        """
        out_since = self._state.out_of_bounds_since
        out_for = int(now - out_since) if out_since is not None else None
        last_rebalance_ts = self._state.last_rebalance_ts
        last_rebalance_age = int(now - last_rebalance_ts) if last_rebalance_ts > 0 else None
        cooldown_left = None
        if last_rebalance_ts > 0:
            cooldown_left = max(0, int(self._settings.cooldown_seconds - (now - last_rebalance_ts)))
        count_last_hour = sum(1 for ts in self._state.rebalance_timestamps if (now - ts) <= 3600)
        return {
            "out_of_bounds_since": out_since,
            "out_of_bounds_for_sec": out_for,
            "last_rebalance_age_sec": last_rebalance_age,
            "cooldown_left_sec": cooldown_left,
            "rebalance_count_last_hour": count_last_hour,
        }

    def _can_rebalance_now(self, now: float) -> bool:
        """
        判断是否满足再平衡频率限制。

        Args:
            now: 当前时间戳。

        Returns:
            是否允许再平衡。
        """
        while self._state.rebalance_timestamps and (now - self._state.rebalance_timestamps[0] > 3600):
            self._state.rebalance_timestamps.popleft()
        return len(self._state.rebalance_timestamps) < self._settings.max_rebalances_per_hour


class LPExecutorState(Enum):
    """
    LP 执行器内部状态机。
    """

    INIT = "init"
    WAITING_TRIGGER = "waiting_trigger"
    PLANNING = "planning"
    OPENING = "opening"
    ACTIVE = "active"
    CLOSING = "closing"
    PAUSED = "paused"
    LIQUIDATING = "liquidating"


@dataclass
class LPOpenContext:
    """
    开仓事务上下文（预算锁与参数快照）。

    Attributes:
        plan: 预算规划结果。
        center_price: 开仓中心价格。
        upper_width_pct: 上半宽度百分比。
        lower_width_pct: 下半宽度百分比。
        reason: 开仓原因。
        created_ts: 创建时间戳。
    """

    plan: BudgetPlan
    center_price: Decimal
    upper_width_pct: Decimal
    lower_width_pct: Decimal
    reason: str
    created_ts: float


@dataclass
class LPCloseContext:
    """
    关仓上下文（用于预算与 PnL 记录）。

    Attributes:
        reason: 关仓原因。
        close_type: 关闭类型。
        position_snapshot: 关仓前仓位快照。
        started_ts: 触发时间戳。
    """

    reason: str
    close_type: CloseType
    position_snapshot: Optional[CLMMPositionInfo]
    started_ts: float


class LPExecutorConfig(ExecutorConfigBase):
    """
    LP 执行器配置（含手续费自动兑换选项）。
    """

    type: Literal["lp_executor"] = "lp_executor"
    model_config = ConfigDict(validate_default=True)
    id: Optional[str] = None
    connector_name: str
    router_connector: str
    trading_pair: str
    pool_address: str = ""

    price_source: str = "pool_info"
    price_connector: str = ""
    price_order_amount_in_base: Decimal = Decimal("1")

    target_price: Decimal = Decimal("0")
    trigger_above: bool = True

    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")

    position_width_pct: Decimal = Decimal("12")
    check_interval_sec: Decimal = Decimal("1")
    status_log_interval_sec: int = 0

    rebalance_seconds: int = 60
    hysteresis_pct: Decimal = Decimal("0.20")
    cooldown_seconds: int = 30
    max_rebalances_per_hour: int = 20
    reopen_delay_sec: int = 5

    collect_fees_interval_sec: int = 1200
    collect_fees_min_quote_value: Decimal = Decimal("0")
    rebalance_collect_fees_ratio_pct: Decimal = Decimal("0")
    rebalance_collect_fees_min_quote_value: Decimal = Decimal("0")
    collect_fees_to_quote: bool = False
    collect_fees_swap_min_quote_value: Decimal = Decimal("0")

    quote_floor: Decimal = Decimal("0.06")
    budget_max_wallet_pct: Decimal = Decimal("0")
    budget_all_or_none: bool = True
    gas_token_symbol: str = "SOL"
    gas_min_reserve: Decimal = Decimal("0.06")

    auto_swap_enabled: bool = True
    target_base_value_pct: Decimal = Decimal("0.5")
    swap_min_quote_value: Decimal = Decimal("0.01")
    swap_safety_buffer_pct: Decimal = Decimal("2")
    swap_timeout_sec: int = 120
    swap_poll_interval_sec: Decimal = Decimal("2")
    swap_slippage_pct: Decimal = Decimal("1")
    swap_retry_attempts: int = 0
    swap_retry_delay_sec: Decimal = Decimal("1")

    open_timeout_sec: int = 180
    close_timeout_sec: int = 180
    pause_on_failure_sec: int = 180
    max_consecutive_failures: int = 4
    orphan_scan_interval_sec: int = 120
    close_dust_quote_value: Decimal = Decimal("0")

    stop_loss_pnl_pct: Decimal = Decimal("0")  # 0-1 比例
    stop_loss_pause_sec: int = 1800
    auto_liquidate_on_stop_loss: bool = True
    reenter_enabled: bool = True


class LPExecutor(ExecutorBase):
    """
    LP 执行器：统一开仓、关仓、再平衡与止损逻辑。
    """

    _logger = None

    @classmethod
    def logger(cls) -> logging.Logger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(
        self,
        strategy: ScriptStrategyBase,
        config: LPExecutorConfig,
        update_interval: float = 1.0,
        max_retries: int = 10,
    ) -> None:
        """
        初始化 LPExecutor。

        Args:
            strategy: 策略实例。
            config: 执行器配置。
            update_interval: 控制循环间隔。
            max_retries: 最大失败重试次数。
        """
        super().__init__(
            strategy=strategy,
            connectors=[config.connector_name, config.router_connector],
            config=config,
            update_interval=update_interval,
        )
        self.config: LPExecutorConfig = config

        self._exchange = config.connector_name
        self._router = config.router_connector
        self._trading_pair = config.trading_pair
        self._base_token, self._quote_token = self._trading_pair.split("-")

        if get_connector_type(self._exchange) != ConnectorType.CLMM:
            raise ValueError(f"LPExecutor requires CLMM connector. Got {self._exchange}.")

        self._state: LPExecutorState = LPExecutorState.INIT
        self._state_ts: float = time.time()
        self._last_tick_ts: float = 0.0
        self._last_status_log_ts: float = 0.0
        self._pause_until_ts: float = 0.0
        self._pause_reason: str = ""
        self._next_open_ts: float = 0.0
        self._pending_reopen: bool = False
        self._last_action_msg: str = ""

        self._entry_price: Optional[Decimal] = None
        self._entry_value_quote: Optional[Decimal] = None
        self._entry_ts: Optional[float] = None
        self._last_position_value_quote: Optional[Decimal] = None
        self._realized_pnl_quote: Decimal = Decimal("0")

        self._open_context: Optional[LPOpenContext] = None
        self._close_context: Optional[LPCloseContext] = None
        self._pending_liquidation: bool = False

        self._last_collect_fees_ts: float = 0.0
        self._last_collect_fees_attempt_ts: float = 0.0
        self._last_orphan_scan_ts: float = 0.0

        self._consecutive_failures: int = 0
        self._last_error: str = ""
        self._pending_failure_reason: str = ""

        self.range_engine = RangeEngine(log_func=self._log)
        self.pool_info_feed = PoolInfoFeed(
            connectors=self.connectors,
            exchange=self._exchange,
            trading_pair=self._trading_pair,
            settings=self._build_pool_info_settings(),
            log_func=self._log,
        )
        self.swap_manager = RouterSwapManager(
            connectors=self.connectors,
            router=self._router,
            trading_pair=self._trading_pair,
            base_token=self._base_token,
            quote_token=self._quote_token,
            log_func=self._log,
            is_paused_func=self._is_paused,
            swap_timeout_sec=self.config.swap_timeout_sec,
            swap_poll_interval_sec=self.config.swap_poll_interval_sec,
            swap_slippage_pct=self.config.swap_slippage_pct,
            swap_retry_attempts=self.config.swap_retry_attempts,
            swap_retry_delay_sec=float(self.config.swap_retry_delay_sec),
        )
        self.position_manager = LPPositionManager(
            connectors=self.connectors,
            exchange=self._exchange,
            trading_pair=self._trading_pair,
            base_token=self._base_token,
            quote_token=self._quote_token,
            pool_address=self.config.pool_address,
            log_func=self._log,
        )
        self.position_manager.set_close_dust_threshold(self.config.close_dust_quote_value)

        self.rebalance_policy = RebalancePolicy(
            settings=self._build_rebalance_settings(),
            log_func=self._log,
        )

        self.budget_manager = BudgetManager(
            settings=self._build_budget_settings(),
            base_token=self._base_token,
            quote_token=self._quote_token,
            get_balances=self._get_wallet_balances,
            swap_func=self._swap_by_base,
            swap_by_quote_func=self._swap_by_quote,
            log_func=self._log,
        )
        self.budget_manager.set_budget_amounts(self.config.base_amount, self.config.quote_amount)

    @property
    def pool_info(self) -> Optional[CLMMPoolInfo]:
        """
        当前池信息。

        Returns:
            池信息；未获取时为 None。
        """
        return self.position_manager.pool_info

    @property
    def position_info(self) -> Optional[CLMMPositionInfo]:
        """
        当前仓位信息。

        Returns:
            仓位信息；无仓位时为 None。
        """
        return self.position_manager.position_info

    @property
    def current_position_id(self) -> Optional[str]:
        """
        当前仓位 ID。

        Returns:
            仓位 ID；无仓位时为 None。
        """
        return self.position_manager.current_position_id

    def _log(self, level: int, msg: str) -> None:
        """
        统一日志输出入口。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        self.logger().log(level, msg)

    def _set_state(self, state: LPExecutorState, reason: str = "") -> None:
        """
        设置内部状态机状态。

        Args:
            state: 新状态。
            reason: 状态说明。
        """
        if state != self._state:
            detail = f" reason={reason}" if reason else ""
            self._log(logging.INFO, f"state_transition: {state.value}{detail}")
            self._state = state
            self._state_ts = time.time()

    def _is_paused(self) -> bool:
        """
        判断执行器是否处于暂停状态。

        Returns:
            是否暂停。
        """
        return time.time() < self._pause_until_ts

    def _entry_trigger_enabled(self) -> bool:
        """
        判断是否启用入场触发。

        Returns:
            是否启用。
        """
        return self.config.target_price is not None and self.config.target_price > 0

    def _entry_trigger_met(self, price: Decimal) -> bool:
        """
        判断是否满足入场触发条件。

        Args:
            price: 当前价格。

        Returns:
            是否触发。
        """
        if not self._entry_trigger_enabled():
            return True
        if self.config.trigger_above:
            return price >= self.config.target_price
        return price <= self.config.target_price

    def _build_pool_info_settings(self) -> PoolInfoFeedSettings:
        """
        构建池信息源配置。

        Returns:
            PoolInfoFeedSettings。
        """
        price_connector = self.config.price_connector or self._router
        return PoolInfoFeedSettings(
            refresh_interval_sec=float(self.config.check_interval_sec),
            price_source=self.config.price_source,
            price_connector=price_connector,
            price_order_amount_in_base=self.config.price_order_amount_in_base,
        )

    def _build_rebalance_settings(self) -> RebalancePolicySettings:
        """
        构建再平衡策略配置。

        Returns:
            RebalancePolicySettings。
        """
        return RebalancePolicySettings(
            hysteresis_pct=self.config.hysteresis_pct,
            rebalance_seconds=self.config.rebalance_seconds,
            cooldown_seconds=self.config.cooldown_seconds,
            max_rebalances_per_hour=self.config.max_rebalances_per_hour,
        )

    def _build_budget_settings(self) -> BudgetSettings:
        """
        构建预算管理器配置。

        Returns:
            BudgetSettings。
        """
        return BudgetSettings(
            quote_floor=self.config.quote_floor,
            budget_max_wallet_pct=self.config.budget_max_wallet_pct,
            auto_swap_enabled=self.config.auto_swap_enabled,
            target_base_value_pct=self.config.target_base_value_pct,
            swap_min_quote_value=self.config.swap_min_quote_value,
            swap_safety_buffer_pct=self.config.swap_safety_buffer_pct,
            gas_token_symbol=self.config.gas_token_symbol,
            gas_min_reserve=self.config.gas_min_reserve,
            max_active_positions=0,
            all_or_none=self.config.budget_all_or_none,
        )

    async def on_start(self):
        """
        执行器启动钩子。
        """
        self.pool_info_feed.start()
        await self.validate_sufficient_balance()

    def on_stop(self):
        """
        执行器停止钩子。
        """
        self.pool_info_feed.stop()

    async def validate_sufficient_balance(self):
        """
        预检查余额（不足时仅记录，不直接终止）。
        """
        base_bal, quote_bal = await self._get_wallet_balances()
        if base_bal <= 0 and quote_bal <= 0:
            self._last_error = "wallet_empty"

    def early_stop(self, keep_position: bool = False):
        """
        外部提前停止执行器。

        Args:
            keep_position: 是否保留仓位。
        """
        if keep_position:
            self.close_type = CloseType.POSITION_HOLD
            self.stop()
            return
        if self.current_position_id:
            self._pending_reopen = False
            safe_ensure_future(self._submit_close(reason="manual_stop", close_type=CloseType.EARLY_STOP))
            return
        self.close_type = CloseType.EARLY_STOP
        self.stop()

    async def control_task(self):
        """
        执行器主循环。
        """
        if not self._strategy.ready_to_trade:
            return

        now = time.time()
        if now - self._last_tick_ts < float(self.config.check_interval_sec):
            return
        self._last_tick_ts = now

        await self._refresh_pool_info()
        self._maybe_log_status(now)

        if self._pending_failure_reason:
            self._handle_pending_failure()

        pending_action = self.position_manager.pending_action
        if pending_action is not None:
            await self._handle_pending_action(now)
            return

        if self.swap_manager.in_progress:
            return

        if self._pending_liquidation:
            await self._handle_liquidation()
            return

        if self._is_paused():
            self._set_state(LPExecutorState.PAUSED, reason=self._pause_reason)
            return

        if self.current_position_id:
            await self._handle_active_position(now)
            return

        await self._handle_no_position(now)

    def _maybe_log_status(self, now: float) -> None:
        """
        按配置间隔输出状态快照，便于排查运行问题。

        Args:
            now: 当前时间戳。
        """
        interval = int(self.config.status_log_interval_sec)
        if interval <= 0:
            return
        if now - self._last_status_log_ts < interval:
            return
        self._last_status_log_ts = now
        ci = self.get_custom_info()
        header = f"LP status snapshot | {self._exchange} | {self._trading_pair}"
        lines = [header]
        lines.extend(
            format_lp_status_lines(
                ci=ci,
                base_token=self._base_token,
                quote_token=self._quote_token,
                max_rebalances_per_hour=self.config.max_rebalances_per_hour,
            )
        )
        self._log(logging.INFO, "\n".join(lines))

    def process_order_failed_event(self, event_tag: int, market: ConnectorBase, event: MarketOrderFailureEvent):
        """
        处理订单失败事件。

        Args:
            event_tag: 事件标签。
            market: 连接器实例。
            event: 失败事件。
        """
        pending = self.position_manager.pending_action
        if pending is None:
            return
        oid = getattr(event, "order_id", None)
        if oid is None:
            return
        if str(oid) == pending.order_id:
            error_message = getattr(event, "error_message", None) or "unknown"
            self._pending_failure_reason = f"order_failed:{error_message}"

    async def _refresh_pool_info(self) -> None:
        """
        刷新池信息并同步至仓位管理器。
        """
        pool_info = await self.pool_info_feed.refresh()
        self.position_manager.pool_info = pool_info

    async def _get_wallet_balances(self) -> tuple[Decimal, Decimal]:
        """
        获取钱包余额。

        Returns:
            base 与 quote 余额。
        """
        base_bal, quote_bal = await self.swap_manager.get_balances()
        return base_bal, quote_bal

    async def _swap_by_base(self, is_buy: bool, amount_base: Decimal, label: str, allow_when_paused: bool) -> bool:
        """
        按 base 数量执行 swap。

        Args:
            is_buy: 是否买入 base。
            amount_base: base 数量。
            label: 日志标签。
            allow_when_paused: 暂停时是否允许。

        Returns:
            是否成功。
        """
        return await self.swap_manager.swap(is_buy, amount_base, label, allow_when_paused)

    async def _swap_by_quote(self, quote_amount: Decimal, label: str, allow_when_paused: bool) -> bool:
        """
        按 quote 数量执行 swap。

        Args:
            quote_amount: quote 数量。
            label: 日志标签。
            allow_when_paused: 暂停时是否允许。

        Returns:
            是否成功。
        """
        return await self.swap_manager.swap_by_quote(quote_amount, label, allow_when_paused)

    async def _handle_pending_action(self, now: float) -> None:
        """
        处理 pending 开/关仓状态。

        Args:
            now: 当前时间戳。
        """
        result = await self.position_manager.handle_pending_action(
            open_timeout_sec=self.config.open_timeout_sec,
            close_timeout_sec=self.config.close_timeout_sec,
        )

        if result.status == PendingStatus.NONE:
            return

        if result.status == PendingStatus.OPEN_CONFIRMED:
            self._confirm_open(result)
            return

        if result.status == PendingStatus.CLOSE_CONFIRMED:
            self._confirm_close()
            return

        if result.status == PendingStatus.OPEN_TIMEOUT:
            self._fail_open("open_timeout")
            return

        if result.status == PendingStatus.CLOSE_TIMEOUT:
            await self._fail_close("close_timeout")
            return

        if result.status == PendingStatus.FETCH_FAILED_TIMEOUT:
            if result.pending and result.pending.operation == PendingOperation.CLOSING:
                await self._fail_close("fetch_timeout")
            else:
                self._fail_open("fetch_timeout")

    def _handle_pending_failure(self) -> None:
        """
        处理事件驱动的失败标记。
        """
        reason = self._pending_failure_reason
        self._pending_failure_reason = ""
        pending = self.position_manager.pending_action
        if pending is None:
            return
        if pending.operation == PendingOperation.OPENING:
            self._fail_open(reason)
        else:
            self._log(logging.WARNING, f"Close failed by event: {reason}")
            safe_ensure_future(self._fail_close(reason))

    def _confirm_open(self, result: PendingResult) -> None:
        """
        确认开仓并更新预算与 PnL 基线。

        Args:
            result: pending 处理结果。
        """
        if result.position is None:
            return
        pending = result.pending
        self.position_manager.confirm_open(result.position)
        lock_id = pending.budget_lock_id if pending else None
        self.budget_manager.commit_lock(lock_id)
        self._record_budget_open(result.position)
        self._set_entry_metrics(result.position)
        self.rebalance_policy.reset_after_open(time.time(), is_reopen=self._pending_reopen)
        self._pending_reopen = False
        self._open_context = None
        self._close_context = None
        self._set_state(LPExecutorState.ACTIVE, reason="open_confirmed")
        self._last_action_msg = "open_confirmed"
        self._reset_failures()

    def _confirm_close(self) -> None:
        """
        确认关仓并更新预算与 PnL。
        """
        close_type = self._close_context.close_type if self._close_context else None
        snapshot = self._close_context.position_snapshot if self._close_context else None
        if snapshot is not None:
            self._record_budget_close(snapshot)
            close_value = self.position_manager.position_value_in_quote(
                snapshot,
                self._resolve_budget_price(snapshot),
                include_fees=True,
            )
            self._record_close_pnl(close_value)
        self.position_manager.complete_close()
        self.rebalance_policy.reset_after_close()
        self._clear_entry_metrics()
        self._close_context = None

        if close_type == CloseType.EARLY_STOP:
            self.close_type = CloseType.EARLY_STOP
            self.stop()
            return

        if close_type == CloseType.STOP_LOSS:
            self._pending_reopen = False
            if self._pending_liquidation:
                self._set_state(LPExecutorState.LIQUIDATING, reason="stop_loss")
                return
            if self.config.reenter_enabled:
                self._pause_until_ts = time.time() + self.config.stop_loss_pause_sec
                self._pause_reason = "stop_loss_pause"
                self._set_state(LPExecutorState.PAUSED, reason="stop_loss")
                return
            self.close_type = CloseType.STOP_LOSS
            self.stop()
            return

        if self._pending_liquidation:
            self._set_state(LPExecutorState.LIQUIDATING, reason="stop_loss")
            return

        if self._pending_reopen:
            self._next_open_ts = time.time() + self.config.reopen_delay_sec
            self._set_state(LPExecutorState.PLANNING, reason="reopen")
            return

        self._set_state(LPExecutorState.WAITING_TRIGGER, reason="close_confirmed")
        self._last_action_msg = "close_confirmed"

    def _fail_open(self, reason: str) -> None:
        """
        处理开仓失败。

        Args:
            reason: 失败原因。
        """
        pending = self.position_manager.pending_action
        lock_id = pending.budget_lock_id if pending else None
        self.budget_manager.release_lock(lock_id, reason=reason)
        self._clear_pending()
        self._register_failure(f"OPEN failed: {reason}")
        self._pause_with_reason(reason)

    async def _fail_close(self, reason: str) -> None:
        """
        处理关仓失败并尝试对账。

        Args:
            reason: 失败原因。
        """
        target_id = self._close_context.position_snapshot.address if self._close_context and self._close_context.position_snapshot else None
        ok = await self.position_manager.reconcile_close_after_failure(target_id=target_id)
        if ok:
            self._confirm_close()
            return
        self._clear_pending()
        self._register_failure(f"CLOSE failed: {reason}")
        self._pause_with_reason(reason)

    def _clear_pending(self) -> None:
        """
        清理 pending 状态与开仓上下文。
        """
        self.position_manager.clear_pending()
        self._open_context = None

    async def _handle_active_position(self, now: float) -> None:
        """
        处理持仓状态下的监控逻辑。

        Args:
            now: 当前时间戳。
        """
        update = await self.position_manager.update_position_info()
        if update.cleared:
            self._record_budget_close(update.position_snapshot) if update.position_snapshot else None
            self._record_close_pnl(update.close_value_quote)
            self._clear_entry_metrics()
            self._set_state(LPExecutorState.WAITING_TRIGGER, reason="position_cleared")
            return

        if self.position_info is None:
            return

        price = self._current_price()
        if price is None or price <= 0:
            return

        await self._maybe_collect_fees(now, price)

        if self._check_stop_loss(price):
            self._pending_liquidation = self.config.auto_liquidate_on_stop_loss
            await self._submit_close(reason="stop_loss", close_type=CloseType.STOP_LOSS)
            return

        decision = self.rebalance_policy.decide(
            now=now,
            current_price=price,
            lower_price=Decimal(str(self.position_info.lower_price)),
            upper_price=Decimal(str(self.position_info.upper_price)),
        )
        if decision and decision.action == ManageAction.OUT_OF_RANGE_REBALANCE:
            if await self._maybe_collect_fees_before_rebalance(price, decision.reason):
                return
            self._pending_reopen = True
            await self._submit_close(reason=f"rebalance:{decision.reason}", close_type=CloseType.COMPLETED)

    async def _handle_no_position(self, now: float) -> None:
        """
        处理空仓状态下的开仓逻辑。

        Args:
            now: 当前时间戳。
        """
        if now < self._next_open_ts:
            self._set_state(LPExecutorState.PAUSED, reason="open_delay")
            return

        if await self._maybe_adopt_position(now):
            return

        price = self._current_price()
        if price is None or price <= 0:
            return

        if not self._pending_reopen and not self._entry_trigger_met(price):
            self._set_state(LPExecutorState.WAITING_TRIGGER, reason="waiting_trigger")
            return

        await self._plan_and_open(price, now)

    async def _plan_and_open(self, price: Decimal, now: float) -> None:
        """
        规划预算并提交开仓。

        Args:
            price: 当前价格。
            now: 当前时间戳。

        说明:
            若自动换仓失败会触发短暂停顿，避免持续重复请求路由。
        """
        if self.pool_info is None:
            return

        half_width = self.config.position_width_pct / Decimal("2")
        half_width = self.range_engine.effective_half_width_pct(
            half_width, self.pool_info, connector_name=self._exchange
        )
        if half_width <= 0:
            self._register_failure("position_width_invalid")
            self._pause_with_reason("position_width_invalid")
            return

        pool_address = await self.position_manager.get_pool_address()
        portfolio = await self.position_manager.summarize_positions(pool_address, price=price)
        plan = await self.budget_manager.plan_open(
            base_amt=self.config.base_amount,
            quote_amt=self.config.quote_amount,
            price=price,
            allow_auto_balance=self.config.auto_swap_enabled,
            label="pre_open:reopen" if self._pending_reopen else "pre_open:initial",
            portfolio=portfolio,
        )

        if not plan.allowed:
            self._last_action_msg = f"open_blocked:{plan.reason}"
            if plan.reason == "auto_swap_failed":
                self._pause_with_reason(plan.reason)
                self._set_state(LPExecutorState.PAUSED, reason=plan.reason)
            else:
                self._set_state(LPExecutorState.PLANNING, reason=plan.reason)
            return

        open_context = LPOpenContext(
            plan=plan,
            center_price=price,
            upper_width_pct=half_width,
            lower_width_pct=half_width,
            reason="reopen" if self._pending_reopen else "initial",
            created_ts=now,
        )
        ok = await self._submit_open(open_context)
        if ok:
            self._open_context = open_context
            self._set_state(LPExecutorState.OPENING, reason=open_context.reason)
            return

        self.budget_manager.release_lock(plan.lock_id, reason="open_submit_failed")
        self._register_failure("open_submit_failed")
        self._pause_with_reason("open_submit_failed")

    async def _submit_open(self, context: LPOpenContext) -> bool:
        """
        提交开仓请求。

        Args:
            context: 开仓上下文。

        Returns:
            是否成功提交。
        """
        result = await self.position_manager.open_position(
            price=float(context.center_price),
            upper_width_pct=float(context.upper_width_pct),
            lower_width_pct=float(context.lower_width_pct),
            base_amount=float(context.plan.base_budget),
            quote_amount=float(context.plan.quote_budget),
            budget_lock_id=context.plan.lock_id,
        )
        if result.submitted:
            self._last_action_msg = f"open_submit:{context.reason}"
            self._entry_price = context.center_price
            return True
        return False

    async def _submit_close(self, reason: str, close_type: CloseType) -> None:
        """
        提交关仓请求。

        Args:
            reason: 关仓原因。
            close_type: 关闭类型。
        """
        if self.position_info is None:
            return
        if self.position_manager.pending_action is not None:
            return
        snapshot = self.position_info
        result = await self.position_manager.close_position()
        if result.submitted:
            self._close_context = LPCloseContext(
                reason=reason,
                close_type=close_type,
                position_snapshot=snapshot,
                started_ts=time.time(),
            )
            self._set_state(LPExecutorState.CLOSING, reason=reason)
            self._last_action_msg = f"close_submit:{reason}"
        else:
            self._register_failure(f"close_submit_failed:{result.error}")
            self._pause_with_reason("close_submit_failed")

    async def _maybe_adopt_position(self, now: float) -> bool:
        """
        尝试接管链上已有仓位（含预算与止损基线校验）。

        Args:
            now: 当前时间戳。

        Returns:
            是否成功接管。
        """
        if now - self._last_orphan_scan_ts < self.config.orphan_scan_interval_sec:
            return False
        self._last_orphan_scan_ts = now
        pool_address = await self.position_manager.get_pool_address()
        position = await self.position_manager.attempt_adopt_existing_position(pool_address)
        if position is None:
            return False
        price = self._resolve_budget_price(position)
        if price <= 0:
            self._pause_with_reason("adopt_price_unavailable")
            self._set_state(LPExecutorState.PAUSED, reason="adopt_price_unavailable")
            self._last_action_msg = "adopt_blocked:price_unavailable"
            return True
        position_value = self.position_manager.position_value_in_quote(position, price, include_fees=True)
        if position_value is None or position_value <= 0:
            self._pause_with_reason("adopt_value_unavailable")
            self._set_state(LPExecutorState.PAUSED, reason="adopt_value_unavailable")
            self._last_action_msg = "adopt_blocked:value_unavailable"
            return True
        budget_value = self.budget_manager.budget_value_in_quote(self.config.base_amount, self.config.quote_amount, price)
        if budget_value <= 0:
            self._pause_with_reason("adopt_budget_zero")
            self._set_state(LPExecutorState.PAUSED, reason="adopt_budget_zero")
            self._last_action_msg = "adopt_blocked:budget_zero"
            return True
        anchor_value = self.budget_manager.anchor_value_quote or budget_value
        budget_cap = min(anchor_value, budget_value)
        if position_value > budget_cap + Decimal("1e-12"):
            self._pause_with_reason("adopt_budget_exceeded")
            self._set_state(LPExecutorState.PAUSED, reason="adopt_budget_exceeded")
            self._last_action_msg = "adopt_blocked:budget_exceeded"
            return True
        if self.config.stop_loss_pnl_pct > 0 and anchor_value > 0:
            total_pnl = position_value - anchor_value
            trigger_loss = anchor_value * self.config.stop_loss_pnl_pct
            if total_pnl <= -trigger_loss:
                self._pause_with_reason("adopt_stop_loss_triggered")
                self._set_state(LPExecutorState.PAUSED, reason="adopt_stop_loss_triggered")
                self._last_action_msg = "adopt_blocked:stop_loss"
                return True
        self.budget_manager.set_budget_amounts(self.config.base_amount, self.config.quote_amount)
        self.budget_manager.record_anchor(anchor_value)
        self.position_manager.confirm_open(position)
        self._record_budget_open(position)
        self._set_entry_metrics(position, entry_value_override=anchor_value)
        self._pending_reopen = False
        self._set_state(LPExecutorState.ACTIVE, reason="adopted")
        self._last_action_msg = "adopted_position"
        return True

    def _current_price(self) -> Optional[Decimal]:
        """
        获取当前估值价格。

        Returns:
            当前价格；不可用时返回 None。
        """
        if self.pool_info_feed.price is not None:
            return self.pool_info_feed.price
        if self.position_info is not None:
            try:
                return Decimal(str(self.position_info.price))
            except Exception:
                return None
        return None

    def _resolve_budget_price(self, position: Optional[CLMMPositionInfo]) -> Decimal:
        """
        获取预算估值价格。

        Args:
            position: 仓位信息。

        Returns:
            估值价格（缺失时返回 0）。
        """
        price = self._current_price()
        if price is None and self._entry_price is not None:
            price = self._entry_price
        return price if price is not None else Decimal("0")

    def _record_budget_open(self, position: CLMMPositionInfo) -> None:
        """
        记录开仓预算账本变动。

        Args:
            position: 仓位信息。
        """
        totals = self.position_manager.position_token_totals(position, include_fees=True)
        if totals is None:
            return
        base_total, quote_total = totals
        price = self._resolve_budget_price(position)
        self.budget_manager.record_open(base_total, quote_total, price)
        value_quote = self.position_manager.position_value_in_quote(position, price, include_fees=True)
        self.budget_manager.record_anchor(value_quote)

    def _record_budget_close(self, position: CLMMPositionInfo) -> None:
        """
        记录关仓预算账本变动。

        Args:
            position: 仓位信息。
        """
        totals = self.position_manager.position_token_totals(position, include_fees=True)
        if totals is None:
            return
        base_total, quote_total = totals
        price = self._resolve_budget_price(position)
        self.budget_manager.record_close(base_total, quote_total, price)

    def _set_entry_metrics(self, position: CLMMPositionInfo, entry_value_override: Optional[Decimal] = None) -> None:
        """
        记录开仓 PnL 基线。

        Args:
            position: 仓位信息。
            entry_value_override: 可选的基线估值，用于接管仓位对齐预算锚定。
        """
        price = self._resolve_budget_price(position)
        value_quote = self.position_manager.position_value_in_quote(position, price, include_fees=True)
        if entry_value_override is not None and entry_value_override > 0:
            value_quote = entry_value_override
        if value_quote is None:
            return
        self._entry_value_quote = value_quote
        self._entry_price = price
        self._entry_ts = time.time()

    def _clear_entry_metrics(self) -> None:
        """
        清理开仓相关指标。
        """
        self._entry_value_quote = None
        self._entry_price = None
        self._entry_ts = None
        self._last_position_value_quote = None

    def _record_close_pnl(self, close_value_quote: Optional[Decimal]) -> None:
        """
        记录平仓 PnL。

        Args:
            close_value_quote: 平仓估值。
        """
        if close_value_quote is None or self._entry_value_quote is None:
            return
        self._realized_pnl_quote += close_value_quote - self._entry_value_quote

    def _record_swap_pnl_delta(
        self,
        price: Decimal,
        base_before: Decimal,
        quote_before: Decimal,
        base_after: Decimal,
        quote_after: Decimal,
        label: str,
    ) -> None:
        """
        记录 swap 造成的净值变化（用于纳入 PnL）。

        Args:
            price: 当前价格。
            base_before: swap 前 base 余额。
            quote_before: swap 前 quote 余额。
            base_after: swap 后 base 余额。
            quote_after: swap 后 quote 余额。
            label: 记录标签。
        """
        if price <= 0:
            return
        value_before = base_before * price + quote_before
        value_after = base_after * price + quote_after
        delta = value_after - value_before
        if delta == 0:
            return
        self._realized_pnl_quote += delta
        self._log(logging.DEBUG, f"Swap pnl delta recorded: label={label} delta={delta:.8f}")

    def _total_pnl_quote(self, current_value: Optional[Decimal]) -> Decimal:
        """
        计算累计 PnL。

        Args:
            current_value: 当前估值。

        Returns:
            累计 PnL。
        """
        if self._entry_value_quote is None or current_value is None:
            return self._realized_pnl_quote
        return self._realized_pnl_quote + (current_value - self._entry_value_quote)

    def _check_stop_loss(self, price: Decimal) -> bool:
        """
        判断是否触发止损（使用 0-1 的 PnL 比例）。

        Args:
            price: 当前价格。

        Returns:
            是否触发止损。
        """
        if self.config.stop_loss_pnl_pct <= 0:
            return False
        anchor_value = self.budget_manager.anchor_value_quote
        if anchor_value is None or anchor_value <= 0:
            return False
        if self.position_info is None:
            return False
        current_value = self.position_manager.position_value_in_quote(self.position_info, price, include_fees=True)
        total_pnl = self._total_pnl_quote(current_value)
        trigger_loss = anchor_value * self.config.stop_loss_pnl_pct
        return total_pnl <= -trigger_loss

    async def _maybe_collect_fees(self, now: float, price: Decimal) -> None:
        """
        定期收取手续费，并按配置将手续费兑换为报价币。

        Args:
            now: 当前时间戳。
            price: 当前价格。
        """
        if self.config.collect_fees_interval_sec <= 0:
            return
        if self.position_info is None:
            return
        if now - self._last_collect_fees_attempt_ts < self.config.collect_fees_interval_sec:
            return
        self._last_collect_fees_attempt_ts = now
        if not self.position_manager.position_has_pending_fees(self.position_info):
            return
        base_fee, quote_fee, fee_value = self._snapshot_pending_fees(price)
        if self.config.collect_fees_min_quote_value > 0 and fee_value < self.config.collect_fees_min_quote_value:
            return
        ok = await self._collect_fees_with_snapshot(
            price=price,
            base_fee=base_fee,
            quote_fee=quote_fee,
            fee_value=fee_value,
            label="interval",
        )
        if ok:
            self._last_collect_fees_ts = now

    async def _maybe_collect_fees_before_rebalance(self, price: Decimal, reason: str) -> bool:
        """
        再平衡前是否先收取手续费，并按配置兑换为报价币。

        Args:
            price: 当前价格。
            reason: 触发原因。

        Returns:
            是否已触发收取手续费。
        """
        if self.config.rebalance_collect_fees_ratio_pct <= 0:
            return False
        if self.position_info is None:
            return False
        if self.config.collect_fees_interval_sec <= 0:
            return False
        if time.time() - self._last_collect_fees_attempt_ts < self.config.collect_fees_interval_sec:
            return False
        fee_value = self.position_manager.position_fee_value_in_quote(self.position_info, price)
        if fee_value is None or fee_value <= 0:
            return False
        if self.config.rebalance_collect_fees_min_quote_value > 0 and fee_value < self.config.rebalance_collect_fees_min_quote_value:
            return False
        total_value = self.position_manager.position_value_in_quote(self.position_info, price, include_fees=True)
        if total_value is None or total_value <= 0:
            return False
        ratio = (fee_value / total_value) * Decimal("100")
        if ratio < self.config.rebalance_collect_fees_ratio_pct:
            return False
        base_fee, quote_fee, fee_value = self._snapshot_pending_fees(price)
        ok = await self._collect_fees_with_snapshot(
            price=price,
            base_fee=base_fee,
            quote_fee=quote_fee,
            fee_value=fee_value,
            label=f"rebalance:{reason}",
        )
        if ok:
            self._last_action_msg = f"collect_fees_before_rebalance:{reason}"
            return True
        return False

    def _snapshot_pending_fees(self, price: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        """
        获取待收取手续费快照并估值。

        Args:
            price: 当前价格。

        Returns:
            (base_fee, quote_fee, fee_value_quote)。
        """
        if self.position_info is None or price <= 0:
            return Decimal("0"), Decimal("0"), Decimal("0")
        try:
            base_fee = Decimal(str(getattr(self.position_info, "base_fee_amount", "0")))
            quote_fee = Decimal(str(getattr(self.position_info, "quote_fee_amount", "0")))
        except Exception:
            return Decimal("0"), Decimal("0"), Decimal("0")
        fee_value = base_fee * price + quote_fee
        return base_fee, quote_fee, fee_value

    async def _collect_fees_with_snapshot(
        self,
        price: Decimal,
        base_fee: Decimal,
        quote_fee: Decimal,
        fee_value: Decimal,
        label: str,
    ) -> bool:
        """
        收取手续费并更新 PnL/预算账本，必要时兑换为报价币。

        Args:
            price: 当前价格。
            base_fee: 待收取 base 手续费。
            quote_fee: 待收取 quote 手续费。
            fee_value: 手续费估值（报价币）。
            label: 日志标签。

        Returns:
            是否成功提交手续费收取。
        """
        ok = await self.position_manager.collect_fees()
        if not ok:
            return False
        if base_fee > 0 or quote_fee > 0:
            self.budget_manager.record_swap_delta(base_fee, quote_fee, price)
        if fee_value > 0:
            self._realized_pnl_quote += fee_value
        if not self.config.collect_fees_to_quote or base_fee <= 0:
            return True
        min_value = self.config.collect_fees_swap_min_quote_value
        if min_value > 0 and base_fee * price < min_value:
            return True
        base_before, quote_before = await self._get_wallet_balances()
        amount_base = min(base_fee, base_before)
        if amount_base <= 0:
            return True
        ok = await self.swap_manager.swap(
            is_buy=False,
            amount_base=amount_base,
            label=f"fees_to_quote:{label}",
            allow_when_paused=False,
        )
        if not ok:
            self._log(logging.WARNING, f"Fee swap to quote failed: label={label} base_fee={base_fee}")
            return True
        base_after, quote_after = await self._get_wallet_balances()
        self.budget_manager.record_swap_delta(base_after - base_before, quote_after - quote_before, price)
        self._record_swap_pnl_delta(
            price=price,
            base_before=base_before,
            quote_before=quote_before,
            base_after=base_after,
            quote_after=quote_after,
            label=f"fees_to_quote:{label}",
        )
        return True

    async def _handle_liquidation(self) -> None:
        """
        止损后的 base -> quote 清算，并将清算成本计入 PnL。
        """
        price = self._current_price()
        if price is None or price <= 0:
            return
        base_before, quote_before = await self._get_wallet_balances()
        if base_before <= 0:
            self._pending_liquidation = False
            self._pending_reopen = False
            if self.config.reenter_enabled:
                self._pause_until_ts = time.time() + self.config.stop_loss_pause_sec
                self._pause_reason = "stop_loss_pause"
                self._set_state(LPExecutorState.PAUSED, reason="stop_loss")
            else:
                self.close_type = CloseType.STOP_LOSS
                self.stop()
            return
        ok = await self.swap_manager.swap(
            is_buy=False,
            amount_base=base_before,
            label="liquidate:stop_loss",
            allow_when_paused=True,
        )
        if ok:
            base_after, quote_after = await self._get_wallet_balances()
            self.budget_manager.record_swap_delta(base_after - base_before, quote_after - quote_before, price)
            self._record_swap_pnl_delta(
                price=price,
                base_before=base_before,
                quote_before=quote_before,
                base_after=base_after,
                quote_after=quote_after,
                label="liquidate:stop_loss",
            )
            self._pending_liquidation = False
            self._pending_reopen = False
            self._last_action_msg = "liquidation_done"
            if self.config.reenter_enabled:
                self._pause_until_ts = time.time() + self.config.stop_loss_pause_sec
                self._pause_reason = "stop_loss_pause"
                self._set_state(LPExecutorState.PAUSED, reason="stop_loss")
            else:
                self.close_type = CloseType.STOP_LOSS
                self.stop()
        else:
            self._pause_with_reason("liquidation_failed")

    def _pause_with_reason(self, reason: str) -> None:
        """
        设置暂停并记录原因。

        Args:
            reason: 暂停原因。
        """
        self._pause_until_ts = time.time() + self.config.pause_on_failure_sec
        self._pause_reason = reason

    def _register_failure(self, reason: str) -> None:
        """
        记录失败次数并设置错误原因。

        Args:
            reason: 失败原因。
        """
        self._consecutive_failures += 1
        self._last_error = reason
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            self._pause_with_reason(reason)

    def _reset_failures(self) -> None:
        """
        重置失败计数。
        """
        self._consecutive_failures = 0
        self._last_error = ""

    @property
    def net_pnl_quote(self) -> Decimal:
        """
        获取累计 PnL。

        Returns:
            PnL（报价币）。
        """
        if self.position_info is None:
            return self._realized_pnl_quote
        price = self._current_price()
        if price is None or price <= 0:
            return self._realized_pnl_quote
        current_value = self.position_manager.position_value_in_quote(self.position_info, price, include_fees=True)
        return self._total_pnl_quote(current_value)

    @property
    def net_pnl_pct(self) -> Decimal:
        """
        获取累计 PnL 比例（相对预算锚定值，0-1）。

        Returns:
            PnL 比例。
        """
        anchor_value = self.budget_manager.anchor_value_quote
        if anchor_value is None or anchor_value <= 0:
            return Decimal("0")
        return self.net_pnl_quote / anchor_value

    @property
    def cum_fees_quote(self) -> Decimal:
        """
        获取未收取手续费价值。

        Returns:
            手续费价值。
        """
        if self.position_info is None:
            return Decimal("0")
        price = self._current_price()
        if price is None or price <= 0:
            return Decimal("0")
        fee_value = self.position_manager.position_fee_value_in_quote(self.position_info, price)
        return fee_value if fee_value is not None else Decimal("0")

    @property
    def filled_amount_quote(self) -> Decimal:
        """
        获取开仓预算价值。

        Returns:
            估算价值。
        """
        return self._entry_value_quote or Decimal("0")

    def get_custom_info(self) -> Dict:
        """
        输出自定义状态信息（含预算与预留配置）。

        Returns:
            状态字典。
        """
        now = time.time()
        price = self._current_price()
        price_value = price if price is not None else Decimal("0")
        price_age = int(now - self.pool_info_feed.last_update_ts) if self.pool_info_feed.last_update_ts > 0 else None
        entry_value = self._entry_value_quote
        total_pnl = None
        unrealized_pnl = None
        if self.position_info is not None and price is not None and price > 0:
            current_value = self.position_manager.position_value_in_quote(self.position_info, price, include_fees=True)
            total_pnl = self._total_pnl_quote(current_value)
            self._last_position_value_quote = current_value
            if self._entry_value_quote is not None:
                unrealized_pnl = current_value - self._entry_value_quote
        else:
            current_value = None
        anchor_value = self.budget_manager.anchor_value_quote
        stop_loss_trigger = None
        stop_loss_distance = None
        if anchor_value is not None and self.config.stop_loss_pnl_pct > 0 and total_pnl is not None:
            trigger_loss = anchor_value * self.config.stop_loss_pnl_pct
            stop_loss_trigger = -trigger_loss
            stop_loss_distance = total_pnl - stop_loss_trigger

        lower_price = None
        upper_price = None
        if self.position_info is not None:
            lower_price = Decimal(str(self.position_info.lower_price))
            upper_price = Decimal(str(self.position_info.upper_price))
        position_base = None
        position_quote = None
        position_fee_base = None
        position_fee_quote = None
        position_total_base = None
        position_total_quote = None
        if self.position_info is not None:
            base_quote = self.position_manager.position_token_totals(self.position_info, include_fees=False)
            total_quote = self.position_manager.position_token_totals(self.position_info, include_fees=True)
            if base_quote is not None:
                position_base, position_quote = base_quote
            if total_quote is not None and base_quote is not None:
                position_total_base, position_total_quote = total_quote
                position_fee_base = total_quote[0] - base_quote[0]
                position_fee_quote = total_quote[1] - base_quote[1]
        base_fee, quote_fee, fee_value = self._snapshot_pending_fees(price_value)
        budget_snapshot = self.budget_manager.ledger_snapshot(price_value)
        effective_quote_floor = self.budget_manager.effective_quote_floor()
        last_collect_age = int(now - self._last_collect_fees_ts) if self._last_collect_fees_ts > 0 else None
        pending_action_age = None
        if self.position_manager.pending_action is not None:
            pending_action_age = int(now - self.position_manager.pending_action.started_ts)
        rebalance_snapshot = self.rebalance_policy.snapshot(now)

        return {
            "lp_state": self._state.value,
            "price": str(price) if price is not None else None,
            "price_source": self.config.price_source,
            "price_age_sec": price_age,
            "entry_price": str(self._entry_price) if self._entry_price is not None else None,
            "entry_age_sec": int(now - self._entry_ts) if self._entry_ts is not None else None,
            "entry_value_quote": str(entry_value) if entry_value is not None else None,
            "current_value_quote": str(current_value) if current_value is not None else None,
            "total_pnl_quote": str(total_pnl) if total_pnl is not None else None,
            "realized_pnl_quote": str(self._realized_pnl_quote),
            "unrealized_pnl_quote": str(unrealized_pnl) if unrealized_pnl is not None else None,
            "net_pnl_pct": str(self.net_pnl_pct) if anchor_value else None,
            "stop_loss_trigger_pnl": str(stop_loss_trigger) if stop_loss_trigger is not None else None,
            "stop_loss_distance_pnl": str(stop_loss_distance) if stop_loss_distance is not None else None,
            "stop_loss_pnl_pct": str(self.config.stop_loss_pnl_pct),
            "anchor_value_quote": str(anchor_value) if anchor_value is not None else None,
            "position_id": self.current_position_id,
            "range_lower": str(lower_price) if lower_price is not None else None,
            "range_upper": str(upper_price) if upper_price is not None else None,
            "position_base": str(position_base) if position_base is not None else None,
            "position_quote": str(position_quote) if position_quote is not None else None,
            "position_fee_base": str(position_fee_base) if position_fee_base is not None else None,
            "position_fee_quote": str(position_fee_quote) if position_fee_quote is not None else None,
            "position_total_base": str(position_total_base) if position_total_base is not None else None,
            "position_total_quote": str(position_total_quote) if position_total_quote is not None else None,
            "pending_action": self.position_manager.pending_action.operation.value if self.position_manager.pending_action else None,
            "pending_action_age_sec": pending_action_age,
            "swap_in_progress": self.swap_manager.in_progress,
            "pause_left_sec": max(0, int(self._pause_until_ts - now)) if self._is_paused() else 0,
            "last_action": self._last_action_msg,
            "last_error": self._last_error,
            "budget_base": str(self.config.base_amount),
            "budget_quote": str(self.config.quote_amount),
            "budget_all_or_none": self.config.budget_all_or_none,
            "budget_wallet_base": str(budget_snapshot["wallet_base"]),
            "budget_wallet_quote": str(budget_snapshot["wallet_quote"]),
            "budget_deployed_base": str(budget_snapshot["deployed_base"]),
            "budget_deployed_quote": str(budget_snapshot["deployed_quote"]),
            "budget_total_value_quote": str(budget_snapshot["total_value_quote"])
            if budget_snapshot["total_value_quote"] is not None
            else None,
            "budget_quote_floor": str(effective_quote_floor),
            "budget_config_quote_floor": str(self.config.quote_floor),
            "gas_token_symbol": self.config.gas_token_symbol,
            "gas_min_reserve": str(self.config.gas_min_reserve),
            "pending_fees_base": str(base_fee),
            "pending_fees_quote": str(quote_fee),
            "pending_fees_value_quote": str(fee_value),
            "collect_fees_interval_sec": self.config.collect_fees_interval_sec,
            "collect_fees_min_quote_value": str(self.config.collect_fees_min_quote_value),
            "collect_fees_to_quote": self.config.collect_fees_to_quote,
            "collect_fees_swap_min_quote_value": str(self.config.collect_fees_swap_min_quote_value),
            "last_collect_fees_age_sec": last_collect_age,
            "rebalance_out_of_bounds_for_sec": rebalance_snapshot.get("out_of_bounds_for_sec"),
            "rebalance_cooldown_left_sec": rebalance_snapshot.get("cooldown_left_sec"),
            "rebalance_count_last_hour": rebalance_snapshot.get("rebalance_count_last_hour"),
            "rebalance_last_rebalance_age_sec": rebalance_snapshot.get("last_rebalance_age_sec"),
        }
