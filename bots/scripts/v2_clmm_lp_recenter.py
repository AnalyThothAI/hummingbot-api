"""
CLMM 固定区间策略（Executor 模式）。
"""

import os
from decimal import Decimal
from typing import Dict, List, Optional, Set

from pydantic import Field

from hummingbot.client.config.config_data_types import ClientFieldData
from .lp.log_filters import set_rate_oracle_warning_suppressed
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType

from .lp.lp_executor import LPExecutor, LPExecutorConfig
from .lp.status_formatter import format_lp_status_lines


class CLMMRecenterFixedConfig(StrategyV2ConfigBase):
    """
    固定区间 LP 策略配置（Executor 模式，含手续费兑换选项）。
    """

    # ---- V2 required / conventional fields ----
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    markets: Dict[str, Set[str]] = Field(default_factory=dict)
    candles_config: List[CandlesConfig] = Field(default_factory=list)
    controllers_config: List[str] = Field(default_factory=list)
    config_update_interval: int = Field(
        default=30,
        gt=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Config update interval (seconds): "),
    )

    # ---- Core targets ----
    connector: str = Field(
        default="meteora/clmm",
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "CLMM connector (e.g. meteora/clmm): "),
    )
    router_connector: str = Field(
        default="jupiter/router",
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Router connector (e.g. jupiter/router): "),
    )
    price_source: str = Field(
        default="pool_info",
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Price source (pool_info/amm_gateway): ",
            is_updatable=True,
        ),
    )
    price_connector: str = Field(
        default="",
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Price connector for amm_gateway (e.g. jupiter/router): ",
            is_updatable=True,
        ),
    )
    price_order_amount_in_base: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Price quote size in base for amm_gateway: ",
            is_updatable=True,
        ),
    )
    trading_pair: str = Field(
        default="SOL-USDC",
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Trading pair (e.g. SOL-USDC): "),
    )
    pool_address: str = Field(
        default="",
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Pool address (optional): "),
    )
    target_price: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Entry trigger price/center (0=disable): ",
            is_updatable=True,
        ),
    )
    trigger_above: bool = Field(
        default=True,
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Trigger when price rises above target (true/false): ",
            is_updatable=True,
        ),
    )

    # ---- Budget ----
    base_amount: Decimal = Field(
        default=Decimal("0"),
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Fixed base budget (can be 0): ", is_updatable=True),
    )
    quote_amount: Decimal = Field(
        default=Decimal("0.2"),
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "Fixed quote budget: ", is_updatable=True),
    )
    quote_floor: Decimal = Field(
        default=Decimal("0.02"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Quote floor reserve (0=disable): ", is_updatable=True),
    )
    budget_max_wallet_pct: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Max budget % of wallet value (0=disable): ", is_updatable=True),
    )
    budget_all_or_none: bool = Field(
        default=True,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Budget all-or-none (strict mode): ", is_updatable=True),
    )
    gas_token_symbol: str = Field(
        default="SOL",
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Gas token symbol (e.g. SOL/ETH/BNB): ", is_updatable=True),
    )
    gas_min_reserve: Decimal = Field(
        default=Decimal("0.06"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Gas token min reserve: ", is_updatable=True),
    )

    # ---- Range / monitoring ----
    position_width_pct: Decimal = Field(
        default=Decimal("12"),
        client_data=ClientFieldData(prompt_on_new=True, prompt=lambda mi: "TOTAL range width % (e.g. 12 means ±6%): ", is_updatable=True),
    )
    check_interval: Decimal = Field(
        default=Decimal("2"),
        gt=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Check interval seconds: ", is_updatable=True),
    )
    status_log_interval_sec: int = Field(
        default=30,
        ge=0,
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Status log interval seconds (0=disable): ",
            is_updatable=True,
        ),
    )
    suppress_rate_oracle_warnings: bool = Field(
        default=True,
        client_data=ClientFieldData(
            prompt_on_new=False,
            prompt=lambda mi: "Suppress rate oracle PnL warnings: ",
            is_updatable=True,
        ),
    )

    # ---- Rebalance (default disabled) ----
    rebalance_seconds: int = Field(
        default=0,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Out-of-range seconds before rebalance: ", is_updatable=True),
    )
    hysteresis_pct: Decimal = Field(
        default=Decimal("0.20"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Out-of-range hysteresis %: ", is_updatable=True),
    )
    cooldown_seconds: int = Field(
        default=30,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Cooldown seconds after rebalance: ", is_updatable=True),
    )
    max_rebalances_per_hour: int = Field(
        default=0,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Max rebalances per hour (0=disable): ", is_updatable=True),
    )
    reopen_delay_sec: int = Field(
        default=5,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Reopen delay seconds after rebalance: ", is_updatable=True),
    )

    # ---- Fees ----
    collect_fees_interval_sec: int = Field(
        default=1200,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Collect fees interval seconds: ", is_updatable=True),
    )
    collect_fees_min_quote_value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Collect fees min value in quote (0=disable): ", is_updatable=True),
    )
    rebalance_collect_fees_ratio_pct: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Rebalance fee ratio threshold % (0=disable): ", is_updatable=True),
    )
    rebalance_collect_fees_min_quote_value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Rebalance fee min value in quote (0=disable): ", is_updatable=True),
    )
    collect_fees_to_quote: bool = Field(
        default=False,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Convert collected fees to quote: ", is_updatable=True),
    )
    collect_fees_swap_min_quote_value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Min fee swap value in quote (0=disable): ", is_updatable=True),
    )

    # ---- Auto swap ----
    auto_swap_enabled: bool = Field(
        default=True,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Enable auto swap: ", is_updatable=True),
    )
    target_base_value_pct: Decimal = Field(
        default=Decimal("0.5"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Target base value ratio (0-1): ", is_updatable=True),
    )
    swap_min_quote_value: Decimal = Field(
        default=Decimal("0.01"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Min swap value in quote: ", is_updatable=True),
    )
    swap_safety_buffer_pct: Decimal = Field(
        default=Decimal("2"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Swap safety buffer %: ", is_updatable=True),
    )
    swap_timeout_sec: int = Field(
        default=120,
        gt=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Swap timeout seconds: ", is_updatable=True),
    )
    swap_poll_interval_sec: Decimal = Field(
        default=Decimal("2"),
        gt=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Swap poll interval seconds: ", is_updatable=True),
    )
    swap_slippage_pct: Decimal = Field(
        default=Decimal("1"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Swap slippage %: ", is_updatable=True),
    )
    swap_retry_attempts: int = Field(
        default=0,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Swap retry attempts (quote errors): ", is_updatable=True),
    )
    swap_retry_delay_sec: Decimal = Field(
        default=Decimal("1"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Swap retry delay seconds: ", is_updatable=True),
    )

    # ---- Reliability ----
    open_timeout_sec: int = Field(
        default=180,
        gt=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Open timeout seconds: ", is_updatable=True),
    )
    close_timeout_sec: int = Field(
        default=180,
        gt=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Close timeout seconds: ", is_updatable=True),
    )
    pause_on_failure_sec: int = Field(
        default=180,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Pause seconds on failure: ", is_updatable=True),
    )
    max_consecutive_failures: int = Field(
        default=4,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Max consecutive failures: ", is_updatable=True),
    )
    orphan_scan_interval_sec: int = Field(
        default=120,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Orphan scan interval seconds: ", is_updatable=True),
    )
    close_dust_quote_value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Close dust quote value: ", is_updatable=True),
    )

    # ---- Stop loss ----
    stop_loss_pnl_pct: Decimal = Field(
        default=Decimal("0.2"),
        ge=0,
        le=1,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Stop loss PnL ratio (0-1, e.g. 0.2): ", is_updatable=True),
    )
    stop_loss_pause_sec: int = Field(
        default=1800,
        ge=0,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Stop loss pause seconds: ", is_updatable=True),
    )
    auto_liquidate_on_stop_loss: bool = Field(
        default=True,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Auto liquidate base to quote on stop loss: ", is_updatable=True),
    )
    reenter_enabled: bool = Field(
        default=True,
        client_data=ClientFieldData(prompt_on_new=False, prompt=lambda mi: "Enable re-entry after stop loss: ", is_updatable=True),
    )


class CLMMRecenterFixed(StrategyV2Base):
    """
    固定区间策略（Executor 模式）。
    """

    @classmethod
    def init_markets(cls, config: CLMMRecenterFixedConfig):
        """
        初始化策略所需的市场订阅。
        """
        markets: Dict[str, Set[str]] = {
            config.connector: {config.trading_pair},
            config.router_connector: {config.trading_pair},
        }
        if config.price_source == "amm_gateway":
            price_connector = config.price_connector or config.router_connector
            markets.setdefault(price_connector, set()).add(config.trading_pair)
        cls.markets = markets

    def __init__(self, connectors: Dict[str, object], config: CLMMRecenterFixedConfig):
        """
        初始化策略（同步性能日志开关）。

        Args:
            connectors: 连接器映射。
            config: 策略配置。
        """
        super().__init__(connectors, config)
        self.config = config
        self._base_token, self._quote_token = self.config.trading_pair.split("-")
        self._lp_executor: Optional[LPExecutor] = None
        set_rate_oracle_warning_suppressed(self.config.suppress_rate_oracle_warnings)
    

    def on_tick(self):
        """
        执行策略主循环（本地管理 LPExecutor，不通过 orchestrator）。
        """
        if not self.ready_to_trade:
            return
        self._ensure_executor_running()

    async def on_stop(self):
        """
        策略停止时回收本地执行器。
        """
        if self._lp_executor is not None:
            self._lp_executor.stop()
        await super().on_stop()

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        """
        生成执行器创建提案。
        """
        return []

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        """
        LP Executor 由自身管理生命周期，默认不下发停止指令。
        """
        return []

    def _ensure_executor_running(self) -> None:
        """
        确保本地 LPExecutor 正常运行。
        """
        if self._lp_executor is not None and not self._lp_executor.is_closed:
            return
        if not self._can_restart_after_stop_loss():
            return
        self._lp_executor = LPExecutor(
            strategy=self,
            config=self._build_executor_config(),
            update_interval=1.0,
            max_retries=10,
        )
        self._lp_executor.start()

    def _get_active_lp_executor(self) -> Optional[LPExecutor]:
        """
        获取当前活跃的本地执行器。
        """
        if self._lp_executor is None or self._lp_executor.is_closed:
            return None
        return self._lp_executor

    def _get_latest_executor(self) -> Optional[LPExecutor]:
        """
        获取最近一次运行的本地执行器。
        """
        return self._lp_executor

    def _can_restart_after_stop_loss(self) -> bool:
        """
        判断是否允许在止损后重新启动执行器。
        """
        if self.config.reenter_enabled:
            return True
        latest = self._get_latest_executor()
        if latest is None:
            return True
        return latest.close_type != CloseType.STOP_LOSS

    def _build_executor_config(self) -> LPExecutorConfig:
        """
        构建 LPExecutor 配置。
        """
        return LPExecutorConfig(
            connector_name=self.config.connector,
            router_connector=self.config.router_connector,
            trading_pair=self.config.trading_pair,
            pool_address=self.config.pool_address,
            price_source=self.config.price_source,
            price_connector=self.config.price_connector,
            price_order_amount_in_base=self.config.price_order_amount_in_base,
            target_price=self.config.target_price,
            trigger_above=self.config.trigger_above,
            base_amount=self.config.base_amount,
            quote_amount=self.config.quote_amount,
            position_width_pct=self.config.position_width_pct,
            check_interval_sec=self.config.check_interval,
            status_log_interval_sec=self.config.status_log_interval_sec,
            rebalance_seconds=self.config.rebalance_seconds,
            hysteresis_pct=self.config.hysteresis_pct,
            cooldown_seconds=self.config.cooldown_seconds,
            max_rebalances_per_hour=self.config.max_rebalances_per_hour,
            reopen_delay_sec=self.config.reopen_delay_sec,
            collect_fees_interval_sec=self.config.collect_fees_interval_sec,
            collect_fees_min_quote_value=self.config.collect_fees_min_quote_value,
            rebalance_collect_fees_ratio_pct=self.config.rebalance_collect_fees_ratio_pct,
            rebalance_collect_fees_min_quote_value=self.config.rebalance_collect_fees_min_quote_value,
            collect_fees_to_quote=self.config.collect_fees_to_quote,
            collect_fees_swap_min_quote_value=self.config.collect_fees_swap_min_quote_value,
            quote_floor=self.config.quote_floor,
            budget_max_wallet_pct=self.config.budget_max_wallet_pct,
            budget_all_or_none=self.config.budget_all_or_none,
            gas_token_symbol=self.config.gas_token_symbol,
            gas_min_reserve=self.config.gas_min_reserve,
            auto_swap_enabled=self.config.auto_swap_enabled,
            target_base_value_pct=self.config.target_base_value_pct,
            swap_min_quote_value=self.config.swap_min_quote_value,
            swap_safety_buffer_pct=self.config.swap_safety_buffer_pct,
            swap_timeout_sec=self.config.swap_timeout_sec,
            swap_poll_interval_sec=self.config.swap_poll_interval_sec,
            swap_slippage_pct=self.config.swap_slippage_pct,
            swap_retry_attempts=self.config.swap_retry_attempts,
            swap_retry_delay_sec=self.config.swap_retry_delay_sec,
            open_timeout_sec=self.config.open_timeout_sec,
            close_timeout_sec=self.config.close_timeout_sec,
            pause_on_failure_sec=self.config.pause_on_failure_sec,
            max_consecutive_failures=self.config.max_consecutive_failures,
            orphan_scan_interval_sec=self.config.orphan_scan_interval_sec,
            close_dust_quote_value=self.config.close_dust_quote_value,
            stop_loss_pnl_pct=self.config.stop_loss_pnl_pct,
            stop_loss_pause_sec=self.config.stop_loss_pause_sec,
            auto_liquidate_on_stop_loss=self.config.auto_liquidate_on_stop_loss,
            reenter_enabled=self.config.reenter_enabled,
        )

    def format_status(self) -> str:
        """
        格式化输出策略状态（包含预算预留信息）。
        """
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        lines: List[str] = []
        header = f"LP Recenter | {self.config.connector} | {self.config.trading_pair}"
        lines.append(header)

        executor = self._get_active_lp_executor()
        if executor is None:
            lines.append("no active executor")
            return "\n".join(lines)

        ci = executor.get_custom_info()
        lines.extend(
            format_lp_status_lines(
                ci=ci,
                base_token=self._base_token,
                quote_token=self._quote_token,
                max_rebalances_per_hour=self.config.max_rebalances_per_hour,
            )
        )

        return "\n".join(lines)
