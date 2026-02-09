import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types
from typing import Any, Dict, Iterable, List, Optional


def _mk_pkg(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    # Mark as namespace package so nested imports work.
    m.__path__ = []  # type: ignore[attr-defined]
    return m


@contextmanager
def _patched_modules(stubs: Dict[str, types.ModuleType], extra_unload: Iterable[str] = ()):
    prev: Dict[str, Optional[types.ModuleType]] = {}
    try:
        for name, mod in stubs.items():
            prev[name] = sys.modules.get(name)
            sys.modules[name] = mod
        yield
    finally:
        for name in extra_unload:
            sys.modules.pop(name, None)
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


@dataclass
class DummyLPPositionExecutorConfig:
    id: str
    controller_id: str
    connector_name: str
    pool_address: str
    trading_pair: str
    lower_price: Decimal
    upper_price: Decimal
    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")
    side: int = 0
    extra_params: Optional[Dict] = None
    keep_position: bool = False
    budget_key: Optional[str] = None
    budget_reservation_id: Optional[str] = None
    timestamp: float = 0.0
    type: str = "lp_position_executor"
    base_token: str = ""
    quote_token: str = ""


@dataclass
class DummyTrackedOrder:
    order_id: str


class DummyCloseType(Enum):
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    EARLY_STOP = "EARLY_STOP"
    POSITION_HOLD = "POSITION_HOLD"


class DummyRunnableStatus(Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    TERMINATED = "TERMINATED"


class DummyMarketEvent(Enum):
    RangePositionLiquidityAdded = 1
    RangePositionLiquidityRemoved = 2
    RangePositionUpdateFailure = 3


@dataclass(frozen=True)
class DummyRangePositionLiquidityAddedEvent:
    order_id: str
    position_address: str
    position_rent: Decimal = Decimal("0")
    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")
    lower_price: Decimal = Decimal("0")
    upper_price: Decimal = Decimal("0")


@dataclass(frozen=True)
class DummyRangePositionLiquidityRemovedEvent:
    order_id: str
    position_rent_refunded: Decimal = Decimal("0")
    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")
    base_fee: Decimal = Decimal("0")
    quote_fee: Decimal = Decimal("0")


@dataclass(frozen=True)
class DummyRangePositionUpdateFailureEvent:
    order_id: str


class DummySourceInfoEventForwarder:
    def __init__(self, callback):
        self.callback = callback


class DummyBudgetCoordinatorRegistry:
    @staticmethod
    def get(_key: str):
        return None


class DummyExecutorBase:
    def __init__(self, strategy, connectors: List[str], config: Any, update_interval: float = 1.0):
        self._strategy = strategy
        self.config = config
        self.connectors = {n: c for n, c in getattr(strategy, "connectors", {}).items() if n in connectors}
        self._status = DummyRunnableStatus.RUNNING

    @property
    def status(self):
        return self._status

    def stop(self):
        self._status = DummyRunnableStatus.TERMINATED

    async def on_start(self):
        return None

    def register_events(self):
        return None

    def unregister_events(self):
        return None


class DummyLPPositionStates(Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    OPENING = "OPENING"
    IN_RANGE = "IN_RANGE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"
    RETRIES_EXCEEDED = "RETRIES_EXCEEDED"


@dataclass
class DummyLPPositionState:
    position_address: Optional[str] = None
    lower_price: Decimal = Decimal("0")
    upper_price: Decimal = Decimal("0")
    base_amount: Decimal = Decimal("0")
    quote_amount: Decimal = Decimal("0")
    base_fee: Decimal = Decimal("0")
    quote_fee: Decimal = Decimal("0")
    position_rent: Decimal = Decimal("0")
    position_rent_refunded: Decimal = Decimal("0")
    active_open_order: Optional[DummyTrackedOrder] = None
    active_close_order: Optional[DummyTrackedOrder] = None
    state: DummyLPPositionStates = DummyLPPositionStates.NOT_ACTIVE
    out_of_range_since: Optional[float] = None

    def update_state(self, current_price: Optional[Decimal] = None, current_time: Optional[float] = None):
        if self.state == DummyLPPositionStates.COMPLETE:
            return
        if self.active_close_order is not None:
            self.state = DummyLPPositionStates.CLOSING
            return
        if self.active_open_order is not None and self.position_address is None:
            self.state = DummyLPPositionStates.OPENING
            return
        if self.position_address and current_price is not None:
            if current_price < self.lower_price or current_price > self.upper_price:
                self.state = DummyLPPositionStates.OUT_OF_RANGE
            else:
                self.state = DummyLPPositionStates.IN_RANGE
        elif self.position_address is None:
            self.state = DummyLPPositionStates.NOT_ACTIVE
        if self.state == DummyLPPositionStates.IN_RANGE:
            self.out_of_range_since = None
        elif self.state == DummyLPPositionStates.OUT_OF_RANGE:
            if self.out_of_range_since is None and current_time is not None:
                self.out_of_range_since = current_time


@dataclass(frozen=True)
class DummyCLMMPosition:
    address: str
    pool_address: str
    lower_price: float
    upper_price: float
    base_token_amount: float
    quote_token_amount: float
    base_fee_amount: float = 0.0
    quote_fee_amount: float = 0.0


class DummyMarketDataProvider:
    def __init__(self, price: float):
        self._price = price

    def get_rate(self, _pair: str) -> float:
        return self._price


class DummyGatewayLpConnector:
    def __init__(self, *, pool_address: str, opened_position: DummyCLMMPosition):
        self._pool_address = pool_address
        self._opened_position = opened_position
        self._has_opened = False
        self.get_user_positions_calls = 0
        self.add_liquidity_calls = 0

    def add_liquidity(self, *, pool_address: str, **_kwargs) -> str:
        assert pool_address == self._pool_address
        self.add_liquidity_calls += 1
        # Simulate: chain-side LP is created, but tx hash / events never make it back.
        self._has_opened = True
        return "range-BUTTCOIN-SOL-1"

    async def get_user_positions(self, pool_address: Optional[str] = None) -> List[DummyCLMMPosition]:
        self.get_user_positions_calls += 1
        if pool_address is not None:
            assert pool_address == self._pool_address
        if not self._has_opened:
            return []
        return [self._opened_position]


class DummyStrategy:
    def __init__(self, *, connector_name: str, connector, price: float):
        self.current_timestamp = 0.0
        self.connectors = {connector_name: connector}
        self.market_data_provider = DummyMarketDataProvider(price=price)


def _load_lp_executor_module(repo_root: Path) -> types.ModuleType:
    module_name = "hummingbot.strategy_v2.executors.lp_position_executor.lp_position_executor"
    file_path = repo_root / "hummingbot" / "hummingbot" / "strategy_v2" / "executors" / "lp_position_executor" / "lp_position_executor.py"
    assert file_path.exists(), file_path
    loader = importlib.machinery.SourceFileLoader(module_name, str(file_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_lp_open_recovers_when_onchain_position_exists_but_event_missing():
    repo_root = Path(__file__).resolve().parents[2]

    # Build stub package/module graph for lp_position_executor imports.
    stubs: Dict[str, types.ModuleType] = {}
    stubs["hummingbot"] = _mk_pkg("hummingbot")
    stubs["hummingbot.core"] = _mk_pkg("hummingbot.core")
    stubs["hummingbot.core.event"] = _mk_pkg("hummingbot.core.event")

    event_forwarder = types.ModuleType("hummingbot.core.event.event_forwarder")
    event_forwarder.SourceInfoEventForwarder = DummySourceInfoEventForwarder  # type: ignore[attr-defined]
    stubs["hummingbot.core.event.event_forwarder"] = event_forwarder

    events = types.ModuleType("hummingbot.core.event.events")
    events.MarketEvent = DummyMarketEvent  # type: ignore[attr-defined]
    events.RangePositionLiquidityAddedEvent = DummyRangePositionLiquidityAddedEvent  # type: ignore[attr-defined]
    events.RangePositionLiquidityRemovedEvent = DummyRangePositionLiquidityRemovedEvent  # type: ignore[attr-defined]
    events.RangePositionUpdateFailureEvent = DummyRangePositionUpdateFailureEvent  # type: ignore[attr-defined]
    stubs["hummingbot.core.event.events"] = events

    logger_mod = types.ModuleType("hummingbot.logger")
    logger_mod.HummingbotLogger = object  # type: ignore[attr-defined]
    stubs["hummingbot.logger"] = logger_mod

    strategy_mod = types.ModuleType("hummingbot.strategy.script_strategy_base")
    strategy_mod.ScriptStrategyBase = object  # type: ignore[attr-defined]
    stubs["hummingbot.strategy"] = _mk_pkg("hummingbot.strategy")
    stubs["hummingbot.strategy.script_strategy_base"] = strategy_mod

    stubs["hummingbot.strategy_v2"] = _mk_pkg("hummingbot.strategy_v2")
    stubs["hummingbot.strategy_v2.budget"] = _mk_pkg("hummingbot.strategy_v2.budget")
    budget_mod = types.ModuleType("hummingbot.strategy_v2.budget.budget_coordinator")
    budget_mod.BudgetCoordinatorRegistry = DummyBudgetCoordinatorRegistry  # type: ignore[attr-defined]
    stubs["hummingbot.strategy_v2.budget.budget_coordinator"] = budget_mod

    stubs["hummingbot.strategy_v2.executors"] = _mk_pkg("hummingbot.strategy_v2.executors")
    stubs["hummingbot.strategy_v2.executors.lp_position_executor"] = _mk_pkg(
        "hummingbot.strategy_v2.executors.lp_position_executor"
    )

    exec_base_mod = types.ModuleType("hummingbot.strategy_v2.executors.executor_base")
    exec_base_mod.ExecutorBase = DummyExecutorBase  # type: ignore[attr-defined]
    stubs["hummingbot.strategy_v2.executors.executor_base"] = exec_base_mod

    lp_types = types.ModuleType("hummingbot.strategy_v2.executors.lp_position_executor.data_types")
    lp_types.LPPositionExecutorConfig = DummyLPPositionExecutorConfig  # type: ignore[attr-defined]
    lp_types.LPPositionState = DummyLPPositionState  # type: ignore[attr-defined]
    lp_types.LPPositionStates = DummyLPPositionStates  # type: ignore[attr-defined]
    stubs["hummingbot.strategy_v2.executors.lp_position_executor.data_types"] = lp_types

    models_base = types.ModuleType("hummingbot.strategy_v2.models.base")
    models_base.RunnableStatus = DummyRunnableStatus  # type: ignore[attr-defined]
    stubs["hummingbot.strategy_v2.models"] = _mk_pkg("hummingbot.strategy_v2.models")
    stubs["hummingbot.strategy_v2.models.base"] = models_base

    models_exec = types.ModuleType("hummingbot.strategy_v2.models.executors")
    models_exec.CloseType = DummyCloseType  # type: ignore[attr-defined]
    models_exec.TrackedOrder = DummyTrackedOrder  # type: ignore[attr-defined]
    stubs["hummingbot.strategy_v2.models.executors"] = models_exec

    module_name = "hummingbot.strategy_v2.executors.lp_position_executor.lp_position_executor"
    with _patched_modules(stubs, extra_unload=(module_name,)):
        lp_exec_mod = _load_lp_executor_module(repo_root)
        LPPositionExecutor = lp_exec_mod.LPPositionExecutor

        connector_name = "meteora/clmm"
        pool_address = "POOL"
        pos = DummyCLMMPosition(
            address="POS1",
            pool_address=pool_address,
            lower_price=0.0003,
            upper_price=0.0005,
            base_token_amount=100.0,
            quote_token_amount=1.0,
        )
        connector = DummyGatewayLpConnector(pool_address=pool_address, opened_position=pos)
        strategy = DummyStrategy(connector_name=connector_name, connector=connector, price=0.0004)

        cfg = DummyLPPositionExecutorConfig(
            id="lp-exec-1",
            controller_id="c1",
            connector_name=connector_name,
            pool_address=pool_address,
            trading_pair="BUTTCOIN-SOL",
            lower_price=Decimal("0.0003"),
            upper_price=Decimal("0.0005"),
            base_amount=Decimal("100"),
            quote_amount=Decimal("1"),
        )
        ex = LPPositionExecutor(strategy=strategy, config=cfg)

        async def _run():
            # Tick 1: submit open.
            strategy.current_timestamp = 0.0
            await ex.control_task()
            assert ex.lp_position_state.state.value == "OPENING"
            assert ex.lp_position_state.position_address is None
            assert connector.add_liquidity_calls == 1

            # Tick 2: enough time passes; executor should reconcile and recover the on-chain position.
            strategy.current_timestamp = 60.0
            await ex.control_task()
            assert ex.lp_position_state.position_address == "POS1"

        asyncio.run(_run())

