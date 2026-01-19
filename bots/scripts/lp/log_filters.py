"""
日志过滤工具（用于抑制非关键告警）。
"""

import logging
from typing import Optional

_PERF_LOGGER_NAME = "hummingbot.client.performance"
_RATE_ORACLE_WARNING_SNIPPET = "Could not find exchange rate"
_rate_oracle_filter: Optional[logging.Filter] = None


class _RateOracleWarningFilter(logging.Filter):
    """
    屏蔽 RateOracle 估值缺失告警。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _RATE_ORACLE_WARNING_SNIPPET in message:
            return False
        return True


def set_rate_oracle_warning_suppressed(suppress: bool) -> None:
    """
    启用/关闭 RateOracle 估值缺失告警过滤。

    Args:
        suppress: True 表示关闭告警。
    """
    global _rate_oracle_filter
    logger = logging.getLogger(_PERF_LOGGER_NAME)
    if suppress:
        if _rate_oracle_filter is None:
            _rate_oracle_filter = _RateOracleWarningFilter()
            logger.addFilter(_rate_oracle_filter)
        return
    if _rate_oracle_filter is not None:
        logger.removeFilter(_rate_oracle_filter)
        _rate_oracle_filter = None
