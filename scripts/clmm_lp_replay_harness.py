#!/usr/bin/env python3
import argparse
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.gateway_swap_executor.data_types import GatewaySwapExecutorConfig
from hummingbot.strategy_v2.executors.lp_position_executor.data_types import LPPositionExecutorConfig, LPPositionStates
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

from bots.controllers.generic.clmm_lp_meteora import CLMMLPMeteoraConfig, CLMMLPMeteoraController


def _to_decimal(value: Optional[float]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


class FakeConnector:
    def __init__(self, base_token: str, quote_token: str, base: Decimal, quote: Decimal) -> None:
        self._base_token = base_token
        self._quote_token = quote_token
        self._balances: Dict[str, Decimal] = {
            base_token: base,
            quote_token: quote,
        }
        self.update_delay_sec: float = 0.0
        self.ready = True

    async def update_balances(self) -> None:
        if self.update_delay_sec > 0:
            await asyncio.sleep(self.update_delay_sec)

    def get_available_balance(self, token: str) -> Decimal:
        return self._balances.get(token, Decimal("0"))

    def set_balances(self, base: Decimal, quote: Decimal) -> None:
        self._balances[self._base_token] = base
        self._balances[self._quote_token] = quote


class FakeMarketDataProvider:
    def __init__(self, connector_name: str, connector: FakeConnector) -> None:
        self.connectors = {connector_name: connector}
        self._prices: Dict[str, Decimal] = {}
        self._time: float = 0.0

    @property
    def ready(self) -> bool:
        return True

    def time(self) -> float:
        return self._time

    def set_time(self, value: float) -> None:
        self._time = value

    def set_price(self, trading_pair: str, price: Decimal) -> None:
        self._prices[trading_pair] = price

    def get_rate(self, trading_pair: str) -> Optional[Decimal]:
        return self._prices.get(trading_pair)

    def initialize_rate_sources(self, _):
        return None


@dataclass
class Step:
    label: str
    advance_sec: float
    executors: List[ExecutorInfo] = field(default_factory=list)
    price: Optional[Decimal] = None
    wallet_base: Optional[Decimal] = None
    wallet_quote: Optional[Decimal] = None
    update_delay_sec: float = 0.0
    sleep_after_sec: float = 0.0


@dataclass
class Scenario:
    name: str
    controller: CLMMLPMeteoraController
    market_data_provider: FakeMarketDataProvider
    connector: FakeConnector
    steps: List[Step]


def _swap_executor_info(
    *,
    executor_id: str,
    controller_id: str,
    timestamp: float,
    connector_name: str,
    trading_pair: str,
    side: TradeType,
    amount: Decimal,
    amount_in_is_quote: bool,
    delta_base: Optional[Decimal],
    delta_quote: Optional[Decimal],
    level_id: str,
    is_active: bool,
    close_type: Optional[CloseType],
) -> ExecutorInfo:
    config = GatewaySwapExecutorConfig(
        timestamp=timestamp,
        connector_name=connector_name,
        trading_pair=trading_pair,
        side=side,
        amount=amount,
        amount_in_is_quote=amount_in_is_quote,
        slippage_pct=Decimal("0.01"),
        pool_address=None,
        level_id=level_id,
        budget_key=controller_id,
    )
    status = RunnableStatus.RUNNING if is_active else RunnableStatus.TERMINATED
    return ExecutorInfo(
        id=executor_id,
        timestamp=timestamp,
        type="gateway_swap_executor",
        status=status,
        config=config,
        net_pnl_pct=Decimal("0"),
        net_pnl_quote=Decimal("0"),
        cum_fees_quote=Decimal("0"),
        filled_amount_quote=Decimal("0"),
        is_active=is_active,
        is_trading=is_active,
        custom_info={
            "delta_base": delta_base,
            "delta_quote": delta_quote,
            "amount_in_is_quote": amount_in_is_quote,
            "amount_in": amount if amount_in_is_quote else None,
            "amount_out": None if amount_in_is_quote else amount,
        },
        close_type=close_type,
        controller_id=controller_id,
    )


def _swap_plan_from_wallet(
    *,
    controller: CLMMLPMeteoraController,
    price: Decimal,
    wallet_base: Decimal,
    wallet_quote: Decimal,
) -> Dict[str, Decimal | TradeType | bool]:
    proposal, reason = controller._build_open_proposal(price, wallet_base, wallet_quote)
    if proposal is None:
        raise RuntimeError(f"open_proposal unavailable: {reason or 'unknown'}")
    delta_base = proposal.delta_base
    delta_quote_value = proposal.delta_quote_value
    if delta_base == 0:
        raise RuntimeError("swap delta is zero; cannot build swap plan")
    if delta_base > 0:
        side = TradeType.BUY
        amount_in_is_quote = True
        amount = delta_quote_value
        delta_quote = -delta_quote_value
    else:
        side = TradeType.SELL
        amount_in_is_quote = False
        amount = abs(delta_base)
        delta_quote = delta_quote_value
    return {
        "delta_base": delta_base,
        "delta_quote": delta_quote,
        "amount": amount,
        "side": side,
        "amount_in_is_quote": amount_in_is_quote,
    }


def _find_wallet_for_swap(
    *,
    controller: CLMMLPMeteoraController,
    price: Decimal,
) -> Dict[str, Decimal]:
    quote_candidate = max(controller.config.position_value_quote, Decimal("1"))
    candidates = [
        (Decimal("0"), quote_candidate),
        (Decimal("1"), Decimal("0")),
        (Decimal("0"), quote_candidate * Decimal("2")),
    ]
    for base, quote in candidates:
        try:
            _swap_plan_from_wallet(
                controller=controller,
                price=price,
                wallet_base=base,
                wallet_quote=quote,
            )
            return {"wallet_base": base, "wallet_quote": quote}
        except RuntimeError:
            continue
    raise RuntimeError("unable to find wallet balances that require a swap")


def _lp_executor_info(
    *,
    executor_id: str,
    controller_id: str,
    timestamp: float,
    connector_name: str,
    pool_address: str,
    trading_pair: str,
    lower_price: Decimal,
    upper_price: Decimal,
    current_price: Decimal,
    base_amount: Decimal,
    quote_amount: Decimal,
    state: str,
    position_address: Optional[str],
    out_of_range_since: Optional[float],
    is_active: bool,
    close_type: Optional[CloseType],
    balance_event: Optional[Dict[str, object]] = None,
) -> ExecutorInfo:
    base_token, quote_token = trading_pair.split("-")
    balance_event_payload = None
    if balance_event is not None:
        event_seq = balance_event.get("seq")
        try:
            event_seq = int(event_seq) if event_seq is not None else 0
        except (TypeError, ValueError):
            event_seq = 0
        event_type = balance_event.get("type")
        delta = balance_event.get("delta")
        if not isinstance(delta, dict):
            delta = {}
        base_delta = delta.get("base")
        quote_delta = delta.get("quote")
        balance_event_payload = {
            "seq": event_seq,
            "type": event_type,
            "delta": {
                "base": float(base_delta) if base_delta is not None else None,
                "quote": float(quote_delta) if quote_delta is not None else None,
            },
        }
    config = LPPositionExecutorConfig(
        timestamp=timestamp,
        connector_name=connector_name,
        pool_address=pool_address,
        trading_pair=trading_pair,
        base_token=base_token,
        quote_token=quote_token,
        lower_price=lower_price,
        upper_price=upper_price,
        base_amount=base_amount,
        quote_amount=quote_amount,
        side=0,
        keep_position=False,
        budget_key=controller_id,
    )
    status = RunnableStatus.RUNNING if is_active else RunnableStatus.TERMINATED
    return ExecutorInfo(
        id=executor_id,
        timestamp=timestamp,
        type="lp_position_executor",
        status=status,
        config=config,
        net_pnl_pct=Decimal("0"),
        net_pnl_quote=Decimal("0"),
        cum_fees_quote=Decimal("0"),
        filled_amount_quote=Decimal("0"),
        is_active=is_active,
        is_trading=is_active,
        custom_info={
            "state": state,
            "position_address": position_address,
            "current_price": float(current_price),
            "lower_price": float(lower_price),
            "upper_price": float(upper_price),
            "base_amount": float(base_amount),
            "quote_amount": float(quote_amount),
            "base_fee": 0.0,
            "quote_fee": 0.0,
            "out_of_range_since": out_of_range_since,
            "balance_event": balance_event_payload,
        },
        close_type=close_type,
        controller_id=controller_id,
    )


def _build_swap_delay_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    start_price: Decimal,
    wallet_base: Decimal,
    wallet_quote: Decimal,
    tick_sec: float,
    delay_ticks: int,
) -> List[Step]:
    swap_plan = _swap_plan_from_wallet(
        controller=controller,
        price=start_price,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
    )
    delta_base = swap_plan["delta_base"]
    delta_quote = swap_plan["delta_quote"]
    swap_amount = swap_plan["amount"]
    side = swap_plan["side"]
    amount_in_is_quote = swap_plan["amount_in_is_quote"]

    swap_executor = _swap_executor_info(
        executor_id="swap-1",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.router_connector,
        trading_pair=trading_pair,
        side=side,
        amount=swap_amount,
        amount_in_is_quote=amount_in_is_quote,
        delta_base=delta_base,
        delta_quote=delta_quote,
        level_id="inventory",
        is_active=False,
        close_type=CloseType.COMPLETED,
    )

    updated_base = wallet_base + delta_base
    updated_quote = wallet_quote + delta_quote

    steps: List[Step] = [
        Step(
            label="entry_decide_swap",
            advance_sec=0.0,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[],
        ),
        Step(
            label="swap_done_balance_stale",
            advance_sec=tick_sec,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_executor],
        ),
    ]

    for idx in range(max(0, delay_ticks - 1)):
        steps.append(
            Step(
                label=f"balance_still_stale_{idx + 1}",
                advance_sec=tick_sec,
                price=start_price,
                wallet_base=wallet_base,
                wallet_quote=wallet_quote,
                executors=[swap_executor],
            )
        )

    steps.append(
        Step(
            label="balance_updated",
            advance_sec=tick_sec,
            price=start_price,
            wallet_base=updated_base,
            wallet_quote=updated_quote,
            executors=[swap_executor],
        )
    )
    steps.append(
        Step(
            label="post_balance_sync",
            advance_sec=tick_sec,
            price=start_price,
            wallet_base=updated_base,
            wallet_quote=updated_quote,
            executors=[swap_executor],
        )
    )
    return steps


def _build_swap_missing_delta_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    start_price: Decimal,
    wallet_base: Decimal,
    wallet_quote: Decimal,
    tick_sec: float,
) -> List[Step]:
    swap_plan = _swap_plan_from_wallet(
        controller=controller,
        price=start_price,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
    )
    swap_executor = _swap_executor_info(
        executor_id="swap-missing",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.router_connector,
        trading_pair=trading_pair,
        side=swap_plan["side"],
        amount=swap_plan["amount"],
        amount_in_is_quote=swap_plan["amount_in_is_quote"],
        delta_base=None,
        delta_quote=None,
        level_id="inventory",
        is_active=False,
        close_type=CloseType.COMPLETED,
    )
    return [
        Step(
            label="swap_done_missing_delta",
            advance_sec=0.0,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_executor],
        ),
        Step(
            label="post_missing_delta",
            advance_sec=tick_sec,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_executor],
        ),
    ]


def _build_balance_timeout_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    start_price: Decimal,
    wallet_base: Decimal,
    wallet_quote: Decimal,
    tick_sec: float,
    timeout_sec: int,
) -> List[Step]:
    swap_plan = _swap_plan_from_wallet(
        controller=controller,
        price=start_price,
        wallet_base=wallet_base,
        wallet_quote=wallet_quote,
    )
    swap_executor = _swap_executor_info(
        executor_id="swap-timeout",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.router_connector,
        trading_pair=trading_pair,
        side=swap_plan["side"],
        amount=swap_plan["amount"],
        amount_in_is_quote=swap_plan["amount_in_is_quote"],
        delta_base=swap_plan["delta_base"],
        delta_quote=swap_plan["delta_quote"],
        level_id="inventory",
        is_active=False,
        close_type=CloseType.COMPLETED,
    )
    return [
        Step(
            label="swap_done_balance_stale",
            advance_sec=0.0,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_executor],
        ),
        Step(
            label="past_timeout",
            advance_sec=float(timeout_sec) + tick_sec,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_executor],
        ),
        Step(
            label="post_timeout",
            advance_sec=tick_sec,
            price=start_price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_executor],
        ),
    ]


def _build_lp_event_missing_delta_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    price: Decimal,
    wallet_base: Decimal,
    wallet_quote: Decimal,
    tick_sec: float,
) -> List[Step]:
    lower = price * Decimal("0.9")
    upper = price * Decimal("1.1")
    lp_executor = _lp_executor_info(
        executor_id="lp-missing",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.connector_name,
        pool_address=controller.config.pool_address,
        trading_pair=trading_pair,
        lower_price=lower,
        upper_price=upper,
        current_price=price,
        base_amount=Decimal("1"),
        quote_amount=price,
        state=LPPositionStates.IN_RANGE.value,
        position_address="pos-missing",
        out_of_range_since=None,
        is_active=True,
        close_type=None,
        balance_event={
            "seq": 1,
            "type": "close",
            "delta": {
                "base": None,
                "quote": None,
            },
        },
    )
    return [
        Step(
            label="lp_event_missing_delta",
            advance_sec=0.0,
            price=price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[lp_executor],
        ),
        Step(
            label="post_lp_missing_delta",
            advance_sec=tick_sec,
            price=price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[lp_executor],
        ),
    ]


def _build_concurrent_swaps_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    price: Decimal,
    wallet_base: Decimal,
    wallet_quote: Decimal,
    tick_sec: float,
) -> List[Step]:
    swap_inventory = _swap_executor_info(
        executor_id="swap-inv",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.router_connector,
        trading_pair=trading_pair,
        side=TradeType.SELL,
        amount=Decimal("1"),
        amount_in_is_quote=False,
        delta_base=None,
        delta_quote=None,
        level_id="inventory",
        is_active=True,
        close_type=None,
    )
    swap_liquidate = _swap_executor_info(
        executor_id="swap-liquidate",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.router_connector,
        trading_pair=trading_pair,
        side=TradeType.SELL,
        amount=Decimal("1"),
        amount_in_is_quote=False,
        delta_base=None,
        delta_quote=None,
        level_id="liquidate",
        is_active=True,
        close_type=None,
    )
    return [
        Step(
            label="concurrent_swaps",
            advance_sec=0.0,
            price=price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_inventory, swap_liquidate],
        ),
        Step(
            label="post_concurrent_swaps",
            advance_sec=tick_sec,
            price=price,
            wallet_base=wallet_base,
            wallet_quote=wallet_quote,
            executors=[swap_inventory, swap_liquidate],
        ),
    ]


def _build_rebalance_loop_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    price: Decimal,
    tick_sec: float,
) -> List[Step]:
    center_price = price
    lower = center_price * Decimal("0.9")
    upper = center_price * Decimal("1.1")
    out_price = upper * Decimal("1.2")
    lp_executor = _lp_executor_info(
        executor_id="lp-rebalance",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.connector_name,
        pool_address=controller.config.pool_address,
        trading_pair=trading_pair,
        lower_price=lower,
        upper_price=upper,
        current_price=out_price,
        base_amount=Decimal("1"),
        quote_amount=center_price,
        state=LPPositionStates.OUT_OF_RANGE.value,
        position_address="pos-rebalance",
        out_of_range_since=-100.0,
        is_active=True,
        close_type=None,
    )
    wallet = _find_wallet_for_swap(controller=controller, price=center_price)
    return [
        Step(
            label="rebalance_trigger_stop",
            advance_sec=0.0,
            price=out_price,
            wallet_base=wallet["wallet_base"],
            wallet_quote=wallet["wallet_quote"],
            executors=[lp_executor],
        ),
        Step(
            label="lp_inactive_wait_reopen",
            advance_sec=tick_sec,
            price=center_price,
            wallet_base=wallet["wallet_base"],
            wallet_quote=wallet["wallet_quote"],
            executors=[],
        ),
        Step(
            label="rebalance_submit_swap_1",
            advance_sec=tick_sec,
            price=center_price,
            wallet_base=wallet["wallet_base"],
            wallet_quote=wallet["wallet_quote"],
            executors=[],
        ),
        Step(
            label="rebalance_submit_swap_2",
            advance_sec=tick_sec,
            price=center_price,
            wallet_base=wallet["wallet_base"],
            wallet_quote=wallet["wallet_quote"],
            executors=[],
        ),
    ]


def _build_stoploss_flow_scenario(
    *,
    controller: CLMMLPMeteoraController,
    trading_pair: str,
    price: Decimal,
    tick_sec: float,
) -> List[Step]:
    lower = price * Decimal("0.9")
    upper = price * Decimal("1.1")
    lp_executor = _lp_executor_info(
        executor_id="lp-stoploss",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.connector_name,
        pool_address=controller.config.pool_address,
        trading_pair=trading_pair,
        lower_price=lower,
        upper_price=upper,
        current_price=price,
        base_amount=Decimal("1"),
        quote_amount=price,
        state=LPPositionStates.IN_RANGE.value,
        position_address="pos-stoploss",
        out_of_range_since=None,
        is_active=True,
        close_type=None,
    )
    price_drop = price * Decimal("0.9")
    lp_close_event = _lp_executor_info(
        executor_id="lp-stoploss",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.connector_name,
        pool_address=controller.config.pool_address,
        trading_pair=trading_pair,
        lower_price=lower,
        upper_price=upper,
        current_price=price_drop,
        base_amount=Decimal("1"),
        quote_amount=price,
        state=LPPositionStates.COMPLETE.value,
        position_address=None,
        out_of_range_since=None,
        is_active=False,
        close_type=CloseType.COMPLETED,
        balance_event={
            "seq": 1,
            "type": "close",
            "delta": {
                "base": Decimal("1"),
                "quote": price,
            },
        },
    )
    liquidation_swap = _swap_executor_info(
        executor_id="swap-liquidation",
        controller_id=controller.config.id,
        timestamp=0.0,
        connector_name=controller.config.router_connector,
        trading_pair=trading_pair,
        side=TradeType.SELL,
        amount=Decimal("1"),
        amount_in_is_quote=False,
        delta_base=-Decimal("1"),
        delta_quote=price_drop,
        level_id="liquidate",
        is_active=False,
        close_type=CloseType.COMPLETED,
    )
    return [
        Step(
            label="anchor_setup",
            advance_sec=0.0,
            price=price,
            wallet_base=Decimal("0"),
            wallet_quote=Decimal("0"),
            executors=[lp_executor],
        ),
        Step(
            label="stoploss_trigger",
            advance_sec=tick_sec,
            price=price_drop,
            wallet_base=Decimal("0"),
            wallet_quote=Decimal("0"),
            executors=[lp_executor],
        ),
        Step(
            label="lp_close_event_balance_stale",
            advance_sec=tick_sec,
            price=price_drop,
            wallet_base=Decimal("0"),
            wallet_quote=Decimal("0"),
            executors=[lp_close_event],
        ),
        Step(
            label="balance_updated",
            advance_sec=tick_sec,
            price=price_drop,
            wallet_base=Decimal("1"),
            wallet_quote=price,
            executors=[],
        ),
        Step(
            label="liquidation_swap_done",
            advance_sec=tick_sec,
            price=price_drop,
            wallet_base=Decimal("0"),
            wallet_quote=price + price_drop,
            executors=[liquidation_swap],
        ),
    ]


def _scenario_swap_delay(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
    )
    steps = _build_swap_delay_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        start_price=_to_decimal(args.price) or Decimal("0"),
        wallet_base=_to_decimal(args.wallet_base) or Decimal("0"),
        wallet_quote=_to_decimal(args.wallet_quote) or Decimal("0"),
        tick_sec=args.tick_sec,
        delay_ticks=max(1, args.delay_ticks),
    )
    return Scenario("swap_delay", controller, market_data_provider, connector, steps)


def _scenario_swap_missing_delta(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
    )
    steps = _build_swap_missing_delta_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        start_price=_to_decimal(args.price) or Decimal("0"),
        wallet_base=_to_decimal(args.wallet_base) or Decimal("0"),
        wallet_quote=_to_decimal(args.wallet_quote) or Decimal("0"),
        tick_sec=args.tick_sec,
    )
    return Scenario("swap_missing_delta", controller, market_data_provider, connector, steps)


def _scenario_balance_timeout(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
    )
    steps = _build_balance_timeout_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        start_price=_to_decimal(args.price) or Decimal("0"),
        wallet_base=_to_decimal(args.wallet_base) or Decimal("0"),
        wallet_quote=_to_decimal(args.wallet_quote) or Decimal("0"),
        tick_sec=args.tick_sec,
        timeout_sec=args.balance_refresh_timeout_sec,
    )
    return Scenario("balance_timeout", controller, market_data_provider, connector, steps)


def _scenario_lp_event_missing(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
    )
    steps = _build_lp_event_missing_delta_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        price=_to_decimal(args.price) or Decimal("0"),
        wallet_base=_to_decimal(args.wallet_base) or Decimal("0"),
        wallet_quote=_to_decimal(args.wallet_quote) or Decimal("0"),
        tick_sec=args.tick_sec,
    )
    return Scenario("lp_event_missing_delta", controller, market_data_provider, connector, steps)


def _scenario_concurrent_swaps(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
    )
    steps = _build_concurrent_swaps_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        price=_to_decimal(args.price) or Decimal("0"),
        wallet_base=_to_decimal(args.wallet_base) or Decimal("0"),
        wallet_quote=_to_decimal(args.wallet_quote) or Decimal("0"),
        tick_sec=args.tick_sec,
    )
    return Scenario("concurrent_swaps", controller, market_data_provider, connector, steps)


def _scenario_rebalance_loop(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
        config_overrides={
            "rebalance_seconds": 0,
            "cooldown_seconds": 0,
            "reopen_delay_sec": 0,
        },
    )
    steps = _build_rebalance_loop_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        price=_to_decimal(args.price) or Decimal("0"),
        tick_sec=args.tick_sec,
    )
    return Scenario("rebalance_loop", controller, market_data_provider, connector, steps)


def _scenario_stoploss_flow(args) -> Scenario:
    controller, market_data_provider, connector = _build_controller(
        connector_name=args.connector_name,
        router_connector=args.router_connector,
        trading_pair=args.trading_pair,
        pool_address=args.pool_address,
        position_value_quote=_to_decimal(args.position_value_quote) or Decimal("0"),
        balance_refresh_interval_sec=args.balance_refresh_interval_sec,
        balance_refresh_timeout_sec=args.balance_refresh_timeout_sec,
        config_overrides={
            "stop_loss_pnl_pct": Decimal("0.02"),
            "stop_loss_pause_sec": 60,
        },
    )
    steps = _build_stoploss_flow_scenario(
        controller=controller,
        trading_pair=args.trading_pair,
        price=_to_decimal(args.price) or Decimal("0"),
        tick_sec=args.tick_sec,
    )
    return Scenario("stoploss_flow", controller, market_data_provider, connector, steps)


async def _run_steps(
    *,
    controller: CLMMLPMeteoraController,
    market_data_provider: FakeMarketDataProvider,
    connector: FakeConnector,
    steps: List[Step],
) -> None:
    current_time = market_data_provider.time()
    for idx, step in enumerate(steps, start=1):
        current_time += step.advance_sec
        market_data_provider.set_time(current_time)
        if step.price is not None:
            market_data_provider.set_price(controller.config.trading_pair, step.price)
        if step.wallet_base is not None and step.wallet_quote is not None:
            connector.set_balances(step.wallet_base, step.wallet_quote)
        connector.update_delay_sec = step.update_delay_sec
        controller.executors_info = step.executors

        await controller.update_processed_data()

        snapshot = controller._latest_snapshot or controller._build_snapshot(market_data_provider.time())
        controller._latest_snapshot = None
        controller._reconcile(snapshot)
        decision = controller._decide(snapshot)
        controller._ctx.apply(decision.patch)

        print(f"[{idx:02d}] t={current_time:.0f} {step.label}")
        print(
            "  decision=%s/%s reason=%s actions=%s"
            % (
                decision.intent.flow.value,
                decision.intent.stage.value,
                decision.intent.reason or "",
                len(decision.actions),
            )
        )

        barrier = controller._ctx.swap.balance_barrier
        if barrier is None:
            barrier_msg = "none"
        else:
            barrier_msg = "exp_base=%s exp_quote=%s" % (
                barrier.expected_delta_base,
                barrier.expected_delta_quote,
            )
        print(
            "  wallet_base=%s wallet_quote=%s awaiting=%s barrier=%s"
            % (
                controller._balance_manager.wallet_base,
                controller._balance_manager.wallet_quote,
                controller._ctx.swap.awaiting_balance_refresh,
                barrier_msg,
            )
        )
        print(
            "  unassigned_base=%s unassigned_quote=%s failure=%s"
            % (
                controller._balance_manager._unassigned_delta_base,
                controller._balance_manager._unassigned_delta_quote,
                controller._ctx.failure.reason or "",
            )
        )

        await asyncio.sleep(step.sleep_after_sec)


async def _run_scenario(scenario: Scenario) -> None:
    print(f"\n=== {scenario.name} ===")
    await _run_steps(
        controller=scenario.controller,
        market_data_provider=scenario.market_data_provider,
        connector=scenario.connector,
        steps=scenario.steps,
    )


def _build_controller(
    *,
    connector_name: str,
    router_connector: str,
    trading_pair: str,
    pool_address: str,
    position_value_quote: Decimal,
    balance_refresh_interval_sec: int,
    balance_refresh_timeout_sec: int,
    config_overrides: Optional[Dict[str, object]] = None,
) -> Tuple[CLMMLPMeteoraController, FakeMarketDataProvider, FakeConnector]:
    config_kwargs: Dict[str, object] = {
        "id": "clmm_lp_harness",
        "connector_name": connector_name,
        "router_connector": router_connector,
        "trading_pair": trading_pair,
        "pool_address": pool_address,
        "position_value_quote": position_value_quote,
        "balance_refresh_interval_sec": balance_refresh_interval_sec,
        "balance_refresh_timeout_sec": balance_refresh_timeout_sec,
    }
    if config_overrides:
        config_kwargs.update(config_overrides)
    config = CLMMLPMeteoraConfig(**config_kwargs)
    base_token, quote_token = trading_pair.split("-")
    connector = FakeConnector(base_token, quote_token, Decimal("0"), Decimal("0"))
    market_data_provider = FakeMarketDataProvider(connector_name, connector)
    actions_queue = asyncio.Queue()
    controller = CLMMLPMeteoraController(
        config=config,
        market_data_provider=market_data_provider,
        actions_queue=actions_queue,
    )
    return controller, market_data_provider, connector


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="CLMM LP replay harness for timing issues.")
    parser.add_argument(
        "--scenario",
        default="swap_delay",
        choices=[
            "swap_delay",
            "swap_missing_delta",
            "balance_timeout",
            "lp_event_missing_delta",
            "concurrent_swaps",
            "rebalance_loop",
            "stoploss_flow",
            "full",
        ],
    )
    parser.add_argument("--trading-pair", default="SOL-USDC")
    parser.add_argument("--connector-name", default="meteora/clmm")
    parser.add_argument("--router-connector", default="jupiter/router")
    parser.add_argument("--pool-address", default="pool")
    parser.add_argument("--price", type=float, default=20.0)
    parser.add_argument("--wallet-base", type=float, default=0.0)
    parser.add_argument("--wallet-quote", type=float, default=1000.0)
    parser.add_argument("--position-value-quote", type=float, default=200.0)
    parser.add_argument("--tick-sec", type=float, default=5.0)
    parser.add_argument("--delay-ticks", type=int, default=2)
    parser.add_argument("--balance-refresh-interval-sec", type=int, default=1)
    parser.add_argument("--balance-refresh-timeout-sec", type=int, default=10)
    args = parser.parse_args()

    builders = {
        "swap_delay": _scenario_swap_delay,
        "swap_missing_delta": _scenario_swap_missing_delta,
        "balance_timeout": _scenario_balance_timeout,
        "lp_event_missing_delta": _scenario_lp_event_missing,
        "concurrent_swaps": _scenario_concurrent_swaps,
        "rebalance_loop": _scenario_rebalance_loop,
        "stoploss_flow": _scenario_stoploss_flow,
    }
    if args.scenario == "full":
        for name in [
            "swap_delay",
            "swap_missing_delta",
            "balance_timeout",
            "lp_event_missing_delta",
            "concurrent_swaps",
            "rebalance_loop",
            "stoploss_flow",
        ]:
            scenario = builders[name](args)
            await _run_scenario(scenario)
    else:
        scenario = builders[args.scenario](args)
        await _run_scenario(scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
