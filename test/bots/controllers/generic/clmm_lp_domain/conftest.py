import sys
import types
from dataclasses import dataclass
from enum import Enum
import logging


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
_ensure_attr(struct_logger_module, "StructLogRecord", object)


common_module = _ensure_module("hummingbot.core.data_type.common")


class _TradeType(Enum):
    BUY = 1
    SELL = 2


_ensure_attr(common_module, "TradeType", _TradeType)

connector_utils_module = _ensure_module("hummingbot.connector.utils")


def _split_hb_trading_pair(trading_pair: str):
    parts = (trading_pair or "").split("-", 1)
    base = parts[0] if len(parts) >= 1 else ""
    quote = parts[1] if len(parts) >= 2 else ""
    return base, quote


_ensure_attr(connector_utils_module, "split_hb_trading_pair", _split_hb_trading_pair)


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
