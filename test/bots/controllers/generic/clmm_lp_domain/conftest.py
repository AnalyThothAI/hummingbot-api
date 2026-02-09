import asyncio
import sys
import types
from dataclasses import dataclass
from enum import Enum
import logging
from decimal import Decimal
from pathlib import Path
from typing import List


# Ensure imports work for both the orchestrator code (`bots.*`) and the vendored
# Hummingbot source (`hummingbot/hummingbot/*`). These tests are often executed
# directly from this folder, where the repo root is not on sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_HBOT_ROOT = _REPO_ROOT / "hummingbot"
for _p in (str(_REPO_ROOT), str(_HBOT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _ensure_module(name: str):
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


def _ensure_attr(module, name: str, value):
    if not hasattr(module, name):
        setattr(module, name, value)


# Minimal logger stubs to avoid importing heavy deps like pandas.
logger_module = _ensure_module("hummingbot.logger")


class _HBLogger(logging.Logger):
    """
    Minimal logger stub compatible with `logging.setLoggerClass(...)`.

    Hummingbot's package `__init__` sets a custom logger class; in tests we stub it
    to avoid importing heavy logger dependencies (e.g. pandas), but it must still
    derive from `logging.Logger` to keep imports working.
    """

    # Keep default logging.Logger behavior; provide `network` as a no-op helper
    # used in some Hummingbot components.
    def network(self, *args, **kwargs):  # pragma: no cover
        return self.info(*args, **kwargs)


_ensure_attr(logger_module, "HummingbotLogger", _HBLogger)

struct_logger_module = _ensure_module("hummingbot.logger.struct_logger")
_ensure_attr(struct_logger_module, "StructLogger", _HBLogger)
_ensure_attr(struct_logger_module, "StructLogRecord", logging.LogRecord)

gateway_pkg = _ensure_module("hummingbot.core.gateway")
gateway_http_client_module = _ensure_module("hummingbot.core.gateway.gateway_http_client")

async_utils_module = _ensure_module("hummingbot.core.utils.async_utils")


def _safe_ensure_future(coro):  # pragma: no cover
    return asyncio.create_task(coro)


_ensure_attr(async_utils_module, "safe_ensure_future", _safe_ensure_future)


class _GatewayHttpClient:  # pragma: no cover
    @classmethod
    def get_instance(cls):
        return cls()

    async def get_connector_chain_network(self, _connector: str):
        return None, None, "stubbed"

    async def get_price(self, *_args, **_kwargs):
        return {"price": None, "error": "stubbed"}


_ensure_attr(gateway_http_client_module, "GatewayHttpClient", _GatewayHttpClient)


common_module = _ensure_module("hummingbot.core.data_type.common")


class _TradeType(Enum):
    BUY = 1
    SELL = 2


_ensure_attr(common_module, "TradeType", _TradeType)


class _MarketDict(dict):
    """Minimal stub for Hummingbot's MarketDict used by controller configs."""

    def add_or_update(self, *_args, **_kwargs):  # pragma: no cover
        return self


_ensure_attr(common_module, "MarketDict", _MarketDict)

connector_utils_module = _ensure_module("hummingbot.connector.utils")


def _split_hb_trading_pair(trading_pair: str):
    parts = (trading_pair or "").split("-", 1)
    base = parts[0] if len(parts) >= 1 else ""
    quote = parts[1] if len(parts) >= 2 else ""
    return base, quote


_ensure_attr(connector_utils_module, "split_hb_trading_pair", _split_hb_trading_pair)


connector_base_module = _ensure_module("hummingbot.connector.connector_base")


class _ConnectorBase:  # pragma: no cover
    """Minimal connector base stub for BudgetCoordinator type hints."""

    def get_available_balance(self, _token: str):
        return 0

    def get_balance(self, _token: str):
        return 0


_ensure_attr(connector_base_module, "ConnectorBase", _ConnectorBase)


executors_module = _ensure_module("hummingbot.strategy_v2.models.executors")


class _CloseType(Enum):
    FAILED = 8
    COMPLETED = 9


_ensure_attr(executors_module, "CloseType", _CloseType)


executor_actions_module = _ensure_module("hummingbot.strategy_v2.models.executor_actions")


@dataclass(frozen=True)
class _ExecutorAction:
    controller_id: str = ""


@dataclass(frozen=True)
class _StopExecutorAction(_ExecutorAction):
    executor_id: str = ""


@dataclass(frozen=True)
class _CreateExecutorAction(_ExecutorAction):
    executor_config: object = None


_ensure_attr(executor_actions_module, "ExecutorAction", _ExecutorAction)
_ensure_attr(executor_actions_module, "StopExecutorAction", _StopExecutorAction)
_ensure_attr(executor_actions_module, "CreateExecutorAction", _CreateExecutorAction)


lp_types_module = _ensure_module("hummingbot.strategy_v2.executors.lp_position_executor.data_types")
_ensure_attr(lp_types_module, "LPPositionExecutorConfig", object)


class _LPPositionStates(Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    OPENING = "OPENING"
    IN_RANGE = "IN_RANGE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"
    RETRIES_EXCEEDED = "RETRIES_EXCEEDED"


_ensure_attr(lp_types_module, "LPPositionStates", _LPPositionStates)

swap_types_module = _ensure_module("hummingbot.strategy_v2.executors.gateway_swap_executor.data_types")
_ensure_attr(swap_types_module, "GatewaySwapExecutorConfig", object)

executors_info_module = _ensure_module("hummingbot.strategy_v2.models.executors_info")
_ensure_attr(executors_info_module, "ExecutorInfo", object)

executors_data_types_module = _ensure_module("hummingbot.strategy_v2.executors.data_types")


@dataclass(frozen=True)
class _ConnectorPair:  # pragma: no cover
    connector_name: str
    trading_pair: str


_ensure_attr(executors_data_types_module, "ConnectorPair", _ConnectorPair)


# Minimal controller config/controller stubs to avoid importing the full Hummingbot client stack.
# Tests under this folder focus on controller domain logic, not full runtime wiring.
controllers_module = _ensure_module("hummingbot.strategy_v2.controllers")

try:  # pragma: no cover - optional dependency in test env
    from pydantic import BaseModel, ConfigDict, Field
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore
    ConfigDict = dict  # type: ignore
    Field = lambda *args, **kwargs: None  # type: ignore


class _ControllerConfigBase(BaseModel):  # pragma: no cover
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    id: str = Field(default="test")
    controller_name: str = Field(default="")
    controller_type: str = Field(default="generic")
    total_amount_quote: Decimal = Field(default=Decimal("0"))
    manual_kill_switch: bool = Field(default=False)
    candles_config: List = Field(default=[])

    def update_markets(self, markets):
        return markets


class _ControllerBase:  # pragma: no cover
    def __init__(self, *_args, **_kwargs):
        pass


_ensure_attr(controllers_module, "ControllerConfigBase", _ControllerConfigBase)
_ensure_attr(controllers_module, "ControllerBase", _ControllerBase)
