import logging
from collections import deque
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

from pydantic import Field, field_validator

from hummingbot.controllers.generic.lp_manager import LPController, LPControllerConfig
from hummingbot.core.data_type.common import MarketDict, PositionAction, PositionMode, PriceType, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionStates
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.twap_executor.data_types import TWAPExecutorConfig, TWAPMode
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


class HedgeState(str, Enum):
    DISABLED = "DISABLED"
    READY = "READY"
    ADJUSTING = "ADJUSTING"
    COOLDOWN = "COOLDOWN"
    SUSPENDED = "SUSPENDED"


class LPHedgeControllerConfig(LPControllerConfig):
    controller_name: str = "lp_hedge"
    candles_config: List[CandlesConfig] = []

    hedge_enabled: bool = Field(
        default=True,
        json_schema_extra={"is_updatable": True},
    )
    hedge_connector_name: str = Field(
        default="binance_perpetual",
        json_schema_extra={"prompt": "Enter the hedge connector: ", "prompt_on_new": True},
    )
    hedge_trading_pair: str = Field(
        default="SOL-USDT",
        json_schema_extra={"prompt": "Enter the hedge trading pair: ", "prompt_on_new": True},
    )
    hedge_position_mode: PositionMode = Field(
        default=PositionMode.HEDGE,
        json_schema_extra={"is_updatable": True},
    )
    hedge_leverage: int = Field(
        default=3,
        ge=1,
        json_schema_extra={"is_updatable": True},
    )
    hedge_price_type: PriceType = Field(
        default=PriceType.MidPrice,
        json_schema_extra={"is_updatable": True},
    )

    hedge_ratio: Decimal = Field(
        default=Decimal("1"),
        ge=0,
        le=1,
        json_schema_extra={"is_updatable": True},
    )
    hedge_include_fees: bool = Field(
        default=True,
        json_schema_extra={"is_updatable": True},
    )
    hedge_allow_long: bool = Field(
        default=False,
        json_schema_extra={"is_updatable": True},
    )
    hedge_close_on_disable: bool = Field(
        default=True,
        json_schema_extra={"is_updatable": True},
    )
    hedge_close_when_no_lp: bool = Field(
        default=True,
        json_schema_extra={"is_updatable": True},
    )
    hedge_include_wallet_base: bool = Field(
        default=True,
        json_schema_extra={"is_updatable": True},
    )

    hedge_min_notional_quote: Decimal = Field(
        default=Decimal("10"),
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_min_available_balance_quote: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_delta_band_quote: Decimal = Field(
        default=Decimal("50"),
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_delta_band_pct: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=1,
        json_schema_extra={"is_updatable": True},
    )
    hedge_delta_hysteresis_quote: Decimal = Field(
        default=Decimal("25"),
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_rebalance_interval_seconds: int = Field(
        default=0,
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_cooldown_seconds: int = Field(
        default=30,
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_max_per_hour: int = Field(
        default=30,
        ge=0,
        json_schema_extra={"is_updatable": True},
    )

    hedge_use_twap_over_quote: Decimal = Field(
        default=Decimal("500"),
        ge=0,
        json_schema_extra={"is_updatable": True},
    )
    hedge_max_order_quote: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        json_schema_extra={"is_updatable": True},
    )

    @field_validator("hedge_price_type", mode="before")
    @classmethod
    def parse_hedge_price_type(cls, v):
        if isinstance(v, PriceType):
            return v
        if isinstance(v, str):
            key = v.strip()
            if key in PriceType.__members__:
                return PriceType[key]
        return PriceType(v)
    hedge_twap_total_duration: int = Field(
        default=60,
        ge=1,
        json_schema_extra={"is_updatable": True},
    )
    hedge_twap_order_interval: int = Field(
        default=5,
        ge=1,
        json_schema_extra={"is_updatable": True},
    )

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets = super().update_markets(markets)
        markets.add_or_update(self.hedge_connector_name, self.hedge_trading_pair)
        return markets


class LPHedgeController(LPController):
    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: LPHedgeControllerConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: LPHedgeControllerConfig = config

        self._hedge_state: HedgeState = HedgeState.READY if config.hedge_enabled else HedgeState.DISABLED
        self._last_hedge_ts: float = 0.0
        self._hedge_timestamps: Deque[float] = deque(maxlen=200)
        self._last_hedge_target: Decimal = Decimal("0")
        self._last_hedge_gap_quote: Decimal = Decimal("0")
        self._last_funding_rate: Optional[Decimal] = None
        self._hedge_collateral_asset = self._get_hedge_collateral_asset()

        self._set_hedge_leverage_and_position_mode()
        self.market_data_provider.initialize_rate_sources([
            self._hedge_connector_pair(),
        ])

    def _hedge_connector_pair(self):
        from hummingbot.strategy_v2.executors.data_types import ConnectorPair

        return ConnectorPair(
            connector_name=self.config.hedge_connector_name,
            trading_pair=self.config.hedge_trading_pair,
        )

    def _get_hedge_collateral_asset(self) -> Optional[str]:
        parts = self.config.hedge_trading_pair.split("-")
        return parts[1] if len(parts) >= 2 else None

    def _set_hedge_leverage_and_position_mode(self):
        try:
            connector = self.market_data_provider.get_connector(self.config.hedge_connector_name)
            if connector is None:
                return
            connector.set_leverage(
                leverage=self.config.hedge_leverage,
                trading_pair=self.config.hedge_trading_pair,
            )
            connector.set_position_mode(self.config.hedge_position_mode)
        except Exception as exc:
            self.logger().warning("Failed to set hedge leverage/position mode: %s", exc)

    def active_executor(self) -> Optional[ExecutorInfo]:
        active = [
            e for e in self.executors_info
            if e.is_active and e.type == "lp_position_executor" and e.controller_id == self.config.id
        ]
        return active[0] if active else None

    def _active_hedge_executor(self) -> Optional[ExecutorInfo]:
        active = [
            e for e in self.executors_info
            if e.is_active
            and e.controller_id == self.config.id
            and e.type in {"order_executor", "twap_executor"}
            and e.connector_name == self.config.hedge_connector_name
            and e.trading_pair == self.config.hedge_trading_pair
        ]
        return active[0] if active else None

    async def update_processed_data(self):
        lp_executor = self.active_executor()
        net_base = self._get_lp_net_base(lp_executor)
        price = self._get_hedge_price()
        current_position = self._get_hedge_position()
        target = self._get_target_hedge_base(net_base)
        gap = target - current_position
        gap_quote = abs(gap) * price if price else Decimal("0")
        band_quote = self._get_effective_delta_band_quote(lp_executor, price)
        available_balance = self._get_hedge_available_balance()
        funding_rate = self._get_funding_rate()
        self._last_funding_rate = funding_rate
        self.processed_data.update({
            "hedge_state": self._hedge_state.value,
            "lp_net_base": net_base,
            "hedge_target_base": target,
            "hedge_position_base": current_position,
            "hedge_gap_base": gap,
            "hedge_gap_quote": gap_quote,
            "hedge_band_quote": band_quote,
            "hedge_available_balance": available_balance,
            "hedge_funding_rate": funding_rate,
        })

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions: List[ExecutorAction] = []
        now = self.market_data_provider.time()
        lp_executor = self.active_executor()
        hedge_executor = self._active_hedge_executor()

        if self.config.manual_kill_switch:
            if lp_executor:
                actions.append(StopExecutorAction(
                    controller_id=self.config.id,
                    executor_id=lp_executor.id,
                    keep_position=False,
                ))
            if hedge_executor:
                actions.append(StopExecutorAction(
                    controller_id=self.config.id,
                    executor_id=hedge_executor.id,
                ))
            if hedge_executor is None:
                close_action = self._build_hedge_close_action(now)
                if close_action:
                    actions.append(close_action)
            self._set_hedge_state(HedgeState.SUSPENDED, "manual_kill_switch")
            return actions

        if not self.config.hedge_enabled and hedge_executor:
            actions.append(StopExecutorAction(
                controller_id=self.config.id,
                executor_id=hedge_executor.id,
            ))
            self._set_hedge_state(HedgeState.SUSPENDED, "hedge_disabled_stop")
            return actions

        lp_actions = super().determine_executor_actions()
        actions.extend(lp_actions)
        if lp_actions:
            self._set_hedge_state(HedgeState.SUSPENDED, "lp_action")
            return actions

        lp_executor_for_hedge = lp_executor
        if lp_executor and not self._lp_state_allows_hedge(lp_executor):
            self._set_hedge_state(HedgeState.SUSPENDED, "lp_state")
            lp_executor_for_hedge = None

        hedge_actions = self._determine_hedge_actions(lp_executor_for_hedge, hedge_executor, now)
        actions.extend(hedge_actions)
        return actions

    def _determine_hedge_actions(
        self,
        lp_executor: Optional[ExecutorInfo],
        hedge_executor: Optional[ExecutorInfo],
        now: float,
    ) -> List[ExecutorAction]:
        if not self.config.hedge_enabled:
            self._set_hedge_state(HedgeState.DISABLED, "disabled")
            if self.config.hedge_close_on_disable:
                close_action = self._build_hedge_close_action(now)
                if close_action:
                    self._record_hedge(now)
                    self._set_hedge_state(HedgeState.SUSPENDED, "hedge_disabled_close")
                    return [close_action]
            return []

        if hedge_executor is not None:
            self._set_hedge_state(HedgeState.ADJUSTING, "hedge_executor_active")
            return []

        price = self._get_hedge_price()
        if price is None or price <= 0:
            self._set_hedge_state(HedgeState.SUSPENDED, "no_price")
            return []

        net_base = self._get_lp_net_base(lp_executor)
        if lp_executor is None and self.config.hedge_close_when_no_lp:
            net_quote = abs(net_base) * price
            if net_quote < self.config.hedge_min_notional_quote:
                close_action = self._build_hedge_close_action(now)
                if close_action:
                    self._record_hedge(now)
                    self._set_hedge_state(HedgeState.SUSPENDED, "no_lp_close")
                    return [close_action]

        if not self._can_hedge_now(now):
            self._set_hedge_state(HedgeState.SUSPENDED, "max_per_hour")
            return []
        target = self._get_target_hedge_base(net_base)
        current_position = self._get_hedge_position()
        delta_position = target - current_position
        delta_quote = abs(delta_position) * price
        self._last_hedge_target = target
        self._last_hedge_gap_quote = delta_quote
        band_quote = self._get_effective_delta_band_quote(lp_executor, price)

        if self._is_in_cooldown(now, delta_quote):
            return []

        if delta_quote < self.config.hedge_min_notional_quote:
            self._set_hedge_state(HedgeState.READY, "below_min_notional")
            return []

        if delta_quote < band_quote and not self._should_force_hedge_by_interval(now):
            self._set_hedge_state(HedgeState.READY, "within_band")
            return []

        intent = self._derive_hedge_action(delta_position)
        if intent is None:
            self._set_hedge_state(HedgeState.READY, "no_action")
            return []
        _, position_action, _ = intent
        if position_action == PositionAction.OPEN and self.config.hedge_min_available_balance_quote > 0:
            available_balance = self._get_hedge_available_balance()
            if available_balance is None or available_balance < self.config.hedge_min_available_balance_quote:
                self._set_hedge_state(HedgeState.SUSPENDED, "low_collateral")
                return []

        action = self._build_hedge_adjust_action(delta_position, price, now, intent)
        if action is None:
            self._set_hedge_state(HedgeState.READY, "no_action")
            return []

        self._record_hedge(now)
        self._set_hedge_state(HedgeState.ADJUSTING, "hedge_execute")
        return [action]

    def _derive_hedge_action(
        self,
        delta_position: Decimal,
    ) -> Optional[Tuple[TradeType, PositionAction, Decimal]]:
        if delta_position == 0:
            return None

        long_amount, short_amount, net_position = self._get_hedge_position_breakdown()
        if delta_position < 0:
            if long_amount > 0:
                side = TradeType.SELL
                position_action = PositionAction.CLOSE
                amount = min(abs(delta_position), long_amount)
            else:
                side = TradeType.SELL
                position_action = PositionAction.OPEN
                amount = abs(delta_position)
        else:
            if short_amount > 0:
                side = TradeType.BUY
                position_action = PositionAction.CLOSE
                amount = min(delta_position, short_amount)
            else:
                side = TradeType.BUY
                position_action = PositionAction.OPEN
                amount = delta_position

        if position_action == PositionAction.CLOSE:
            max_close = abs(net_position) if net_position != 0 else max(long_amount, short_amount)
            if max_close <= 0:
                return None
            amount = min(amount, max_close)
        return side, position_action, amount

    def _build_hedge_adjust_action(
        self,
        delta_position: Decimal,
        price: Decimal,
        now: float,
        intent: Optional[Tuple[TradeType, PositionAction, Decimal]] = None,
    ) -> Optional[CreateExecutorAction]:
        if intent is None:
            intent = self._derive_hedge_action(delta_position)
        if intent is None:
            return None
        side, position_action, amount = intent

        if side == TradeType.BUY and position_action == PositionAction.OPEN and not self.config.hedge_allow_long:
            self.logger().info("Hedge long disabled. Skipping long open.")
            return None

        amount = self._quantize_hedge_amount(amount)
        if amount <= 0:
            return None

        total_quote = amount * price
        if self.config.hedge_max_order_quote > 0 and total_quote > self.config.hedge_max_order_quote:
            amount = self._quantize_hedge_amount(self.config.hedge_max_order_quote / price)
            if amount <= 0:
                return None
            total_quote = amount * price
        if self.config.hedge_use_twap_over_quote > 0 and total_quote >= self.config.hedge_use_twap_over_quote:
            config = TWAPExecutorConfig(
                timestamp=now,
                connector_name=self.config.hedge_connector_name,
                trading_pair=self.config.hedge_trading_pair,
                side=side,
                leverage=self.config.hedge_leverage,
                total_amount_quote=total_quote,
                total_duration=self.config.hedge_twap_total_duration,
                order_interval=self.config.hedge_twap_order_interval,
                mode=TWAPMode.TAKER,
            )
        else:
            config = OrderExecutorConfig(
                timestamp=now,
                connector_name=self.config.hedge_connector_name,
                trading_pair=self.config.hedge_trading_pair,
                side=side,
                amount=amount,
                price=price,
                leverage=self.config.hedge_leverage,
                position_action=position_action,
                execution_strategy=ExecutionStrategy.MARKET,
            )

        return CreateExecutorAction(controller_id=self.config.id, executor_config=config)

    def _build_hedge_close_action(self, now: float) -> Optional[CreateExecutorAction]:
        price = self._get_hedge_price()
        if price is None or price <= 0:
            return None
        long_amount, short_amount, _ = self._get_hedge_position_breakdown()
        if long_amount == 0 and short_amount == 0:
            return None
        if long_amount >= short_amount:
            side = TradeType.SELL
            amount = long_amount
        else:
            side = TradeType.BUY
            amount = short_amount
        position_action = PositionAction.CLOSE
        amount = self._quantize_hedge_amount(amount)
        if amount <= 0:
            return None
        config = OrderExecutorConfig(
            timestamp=now,
            connector_name=self.config.hedge_connector_name,
            trading_pair=self.config.hedge_trading_pair,
            side=side,
            amount=amount,
            price=price,
            leverage=self.config.hedge_leverage,
            position_action=position_action,
            execution_strategy=ExecutionStrategy.MARKET,
        )
        return CreateExecutorAction(controller_id=self.config.id, executor_config=config)

    def _get_lp_net_base(self, lp_executor: Optional[ExecutorInfo]) -> Decimal:
        base_amount = Decimal("0")
        if lp_executor is not None:
            custom = lp_executor.custom_info or {}
            base_amount = Decimal(str(custom.get("base_amount", 0)))
            if self.config.hedge_include_fees:
                base_amount += Decimal(str(custom.get("base_fee", 0)))
        if self.config.hedge_include_wallet_base:
            base_amount += self._get_wallet_base_balance()
        return base_amount

    def _get_lp_value_quote(self, lp_executor: Optional[ExecutorInfo], price: Optional[Decimal]) -> Optional[Decimal]:
        if lp_executor is None or price is None or price <= 0:
            return None
        custom = lp_executor.custom_info or {}
        base_amount = Decimal(str(custom.get("base_amount", 0)))
        quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        if self.config.hedge_include_fees:
            base_amount += Decimal(str(custom.get("base_fee", 0)))
            quote_amount += Decimal(str(custom.get("quote_fee", 0)))
        return base_amount * price + quote_amount

    def _get_effective_delta_band_quote(
        self,
        lp_executor: Optional[ExecutorInfo],
        price: Optional[Decimal],
    ) -> Decimal:
        band_quote = self.config.hedge_delta_band_quote
        if self.config.hedge_delta_band_pct <= 0:
            return band_quote
        lp_value = self._get_lp_value_quote(lp_executor, price)
        if lp_value is None:
            return band_quote
        return max(band_quote, lp_value * self.config.hedge_delta_band_pct)

    def _get_hedge_position(self) -> Decimal:
        return self._get_hedge_position_breakdown()[2]

    def _get_hedge_position_breakdown(self) -> Tuple[Decimal, Decimal, Decimal]:
        long_amount = Decimal("0")
        short_amount = Decimal("0")
        for position in self.positions_held:
            if (position.connector_name == self.config.hedge_connector_name and
                    position.trading_pair == self.config.hedge_trading_pair):
                if position.side == TradeType.BUY:
                    long_amount += position.amount
                else:
                    short_amount += position.amount
        net_position = long_amount - short_amount
        return long_amount, short_amount, net_position

    def _get_target_hedge_base(self, net_base: Decimal) -> Decimal:
        target = -net_base * self.config.hedge_ratio
        if not self.config.hedge_allow_long and target > 0:
            return Decimal("0")
        return target

    def _get_hedge_price(self) -> Optional[Decimal]:
        price = self.market_data_provider.get_price_by_type(
            self.config.hedge_connector_name,
            self.config.hedge_trading_pair,
            self.config.hedge_price_type,
        )
        if price is None:
            return None
        try:
            return Decimal(str(price))
        except Exception:
            return None

    def _get_hedge_available_balance(self) -> Optional[Decimal]:
        if not self._hedge_collateral_asset:
            return None
        try:
            balance = self.market_data_provider.get_available_balance(
                self.config.hedge_connector_name,
                self._hedge_collateral_asset,
            )
            return Decimal(str(balance))
        except Exception:
            return None

    def _get_funding_rate(self) -> Optional[Decimal]:
        try:
            funding_info = self.market_data_provider.get_funding_info(
                self.config.hedge_connector_name,
                self.config.hedge_trading_pair,
            )
        except Exception:
            return None
        if funding_info is None:
            return None
        for attr in ("funding_rate", "rate"):
            value = getattr(funding_info, attr, None)
            if value is not None:
                try:
                    return Decimal(str(value))
                except Exception:
                    return None
        return None

    def _get_wallet_base_balance(self) -> Decimal:
        try:
            balance = self.market_data_provider.get_balance(self.config.connector_name, self._base_token)
            return Decimal(str(balance))
        except Exception:
            return Decimal("0")

    def _quantize_hedge_amount(self, amount: Decimal) -> Decimal:
        try:
            return self.market_data_provider.quantize_order_amount(
                self.config.hedge_connector_name,
                self.config.hedge_trading_pair,
                amount,
            )
        except Exception:
            return amount

    def _lp_state_allows_hedge(self, lp_executor: Optional[ExecutorInfo]) -> bool:
        if lp_executor is None:
            return False
        state = (lp_executor.custom_info or {}).get("state")
        return state in {LPPositionStates.IN_RANGE.value, LPPositionStates.OUT_OF_RANGE.value}

    def _set_hedge_state(self, new_state: HedgeState, reason: str = ""):
        if self._hedge_state == new_state:
            return
        self._hedge_state = new_state
        if reason:
            self.logger().info("Hedge state -> %s (%s)", new_state.value, reason)
        else:
            self.logger().info("Hedge state -> %s", new_state.value)

    def _record_hedge(self, now: float):
        self._last_hedge_ts = now
        self._hedge_timestamps.append(now)

    def _can_hedge_now(self, now: float) -> bool:
        if self.config.hedge_max_per_hour <= 0:
            return True
        while self._hedge_timestamps and (now - self._hedge_timestamps[0] > 3600):
            self._hedge_timestamps.popleft()
        return len(self._hedge_timestamps) < self.config.hedge_max_per_hour

    def _should_force_hedge_by_interval(self, now: float) -> bool:
        interval = self.config.hedge_rebalance_interval_seconds
        if interval <= 0:
            return False
        return (now - self._last_hedge_ts) >= interval

    def _is_in_cooldown(self, now: float, delta_quote: Decimal) -> bool:
        if self.config.hedge_cooldown_seconds <= 0:
            return False
        if (now - self._last_hedge_ts) < self.config.hedge_cooldown_seconds:
            hysteresis = self._normalized_hysteresis()
            if delta_quote <= hysteresis:
                self._set_hedge_state(HedgeState.READY, "cooldown_within_hysteresis")
            else:
                self._set_hedge_state(HedgeState.COOLDOWN, "cooldown")
            return True
        return False

    def _normalized_hysteresis(self) -> Decimal:
        hysteresis = self.config.hedge_delta_hysteresis_quote
        if hysteresis <= 0:
            return self.config.hedge_delta_band_quote
        return min(hysteresis, self.config.hedge_delta_band_quote)

    def get_custom_info(self) -> Dict:
        available_balance = self._get_hedge_available_balance()
        price = self._get_hedge_price()
        band_quote = self._get_effective_delta_band_quote(self.active_executor(), price)
        return {
            "hedge_state": self._hedge_state.value,
            "hedge_target_base": float(self._last_hedge_target),
            "hedge_gap_quote": float(self._last_hedge_gap_quote),
            "hedge_band_quote": float(band_quote),
            "hedge_position_base": float(self._get_hedge_position()),
            "hedge_available_balance": float(available_balance) if available_balance is not None else None,
            "hedge_funding_rate": float(self._last_funding_rate) if self._last_funding_rate is not None else None,
        }

    def to_format_status(self) -> List[str]:
        status = super().to_format_status() or []

        lp_executor = self.active_executor()
        custom = lp_executor.custom_info if lp_executor else {}
        base_amount = Decimal(str(custom.get("base_amount", 0)))
        quote_amount = Decimal(str(custom.get("quote_amount", 0)))
        base_fee = Decimal(str(custom.get("base_fee", 0)))
        quote_fee = Decimal(str(custom.get("quote_fee", 0)))
        lp_state = custom.get("state", "N/A")
        wallet_base = self._get_wallet_base_balance() if self.config.hedge_include_wallet_base else Decimal("0")

        net_base = self._get_lp_net_base(lp_executor)
        price = self._get_hedge_price()
        price_value = price if price is not None else Decimal("0")
        current_position = self._get_hedge_position()
        target = self._get_target_hedge_base(net_base)
        gap = target - current_position
        gap_quote = abs(gap) * price_value
        band_quote = self._get_effective_delta_band_quote(lp_executor, price_value)
        available_balance = self._get_hedge_available_balance()
        funding_rate = self._last_funding_rate

        now = self.market_data_provider.time()
        cooldown_ok = (
            self.config.hedge_cooldown_seconds <= 0
            or (now - self._last_hedge_ts) >= self.config.hedge_cooldown_seconds
        )
        min_notional_ok = gap_quote >= self.config.hedge_min_notional_quote
        outside_band = gap_quote >= band_quote
        interval_due = (
            self.config.hedge_rebalance_interval_seconds > 0
            and (now - self._last_hedge_ts) >= self.config.hedge_rebalance_interval_seconds
        )
        max_per_hour_ok = self._can_hedge_now(now)
        collateral_ok = True
        if self.config.hedge_min_available_balance_quote > 0:
            collateral_ok = available_balance is not None and (
                available_balance >= self.config.hedge_min_available_balance_quote
            )

        def fmt_dec(value: Decimal, precision: int = 6) -> str:
            try:
                return f"{value:.{precision}f}"
            except Exception:
                return "n/a"

        status.extend([
            "",
            "=" * 65,
            f"LP HEDGE CONTROLLER: {self.config.hedge_trading_pair} @ "
            f"{fmt_dec(price_value, 6)} {self._hedge_collateral_asset or ''}".rstrip(),
            "=" * 65,
            f"  Hedge State:      {self._hedge_state.value}",
            f"  LP State:         {lp_state}",
            "",
            f"  LP Base:          {fmt_dec(base_amount)} {self._base_token}",
            f"  LP Quote:         {fmt_dec(quote_amount)} {self._quote_token}",
            f"  LP Fees:          {fmt_dec(base_fee)} {self._base_token} / {fmt_dec(quote_fee)} {self._quote_token}",
            f"  Wallet Base:      {fmt_dec(wallet_base)} {self._base_token}",
            "",
            f"  Net Base:         {fmt_dec(net_base)} {self._base_token}",
            f"  Hedge Ratio:      {self.config.hedge_ratio:.1%}",
            f"  Target Hedge:     {fmt_dec(target)} {self._base_token}",
            f"  Current Hedge:    {fmt_dec(current_position)} {self._base_token}",
            f"  Gap:              {fmt_dec(gap)} {self._base_token}  "
            f"({fmt_dec(gap_quote, 2)} {self._hedge_collateral_asset or ''})".rstrip(),
            f"  Band (Quote):     {fmt_dec(band_quote, 2)} {self._hedge_collateral_asset or ''}".rstrip(),
            "",
            f"  Funding Rate:     {fmt_dec(funding_rate, 6) if funding_rate is not None else 'n/a'}",
            f"  Perp Balance:     {fmt_dec(available_balance, 2) if available_balance is not None else 'n/a'} "
            f"{self._hedge_collateral_asset or ''}".rstrip(),
            "",
            "  Trading Conditions:",
            f"    Cooldown OK:            {'OK' if cooldown_ok else 'NO'}",
            f"    Min Notional OK:        {'OK' if min_notional_ok else 'NO'}",
            f"    Outside Band:           {'YES' if outside_band else 'NO'}",
            f"    Interval Due:           {'YES' if interval_due else 'NO'}",
            f"    Max Per Hour OK:        {'OK' if max_per_hour_ok else 'NO'}",
            f"    Collateral OK:          {'OK' if collateral_ok else 'NO'}",
            "=" * 65,
        ])
        return status
