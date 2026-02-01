import sys
import types
from dataclasses import dataclass
from enum import Enum


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


class _HBLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


_ensure_attr(logger_module, "HummingbotLogger", _HBLogger)

struct_logger_module = _ensure_module("hummingbot.logger.struct_logger")
_ensure_attr(struct_logger_module, "StructLogger", _HBLogger)
_ensure_attr(struct_logger_module, "StructLogRecord", object)


common_module = _ensure_module("hummingbot.core.data_type.common")


class _TradeType(Enum):
    BUY = 1
    SELL = 2


_ensure_attr(common_module, "TradeType", _TradeType)


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
