"""
CLMM 区间计算与 bin 宽度修正工具。
"""

import logging
import math
from decimal import Decimal
from typing import Any, Callable, Optional

LogFunc = Callable[[int, str], None]


class RangeEngine:
    """
    CLMM 区间计算与 Meteora bin 限制修正。
    """

    DLMM_CONNECTOR_KEYWORDS = ("meteora", "dlmm")

    def __init__(self, max_position_bins: int = 69, log_func: Optional[LogFunc] = None) -> None:
        """
        初始化区间引擎。

        Args:
            max_position_bins: 允许的最大 bin 数量。
            log_func: 日志函数（可选）。
        """
        self._max_bins = max_position_bins
        self._log = log_func or self._default_log

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数，使用模块 logger 输出。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    def _is_dlmm_connector(self, connector_name: Optional[str]) -> bool:
        """
        判断连接器是否为 DLMM 类型（用于 bin clamp 判断）。

        Args:
            connector_name: 连接器名称（如 meteora/clmm）。

        Returns:
            是否为 DLMM 连接器。
        """
        if not connector_name:
            return False
        lowered = connector_name.lower()
        return any(keyword in lowered for keyword in self.DLMM_CONNECTOR_KEYWORDS)

    def estimate_bins_for_half_width_pct(self, half_width_pct: Decimal, bin_step: int) -> Optional[int]:
        """
        估算给定半宽度对应的 bin 数量（近似）。

        Args:
            half_width_pct: 区间半宽度百分比。
            bin_step: bin step（bps）。

        Returns:
            估算 bin 数量；无法估算时返回 None。
        """
        try:
            if half_width_pct <= 0 or bin_step <= 0:
                return None
            half_width = float(half_width_pct) / 100.0
            if half_width >= 1:
                return None
            ratio = (1.0 + half_width) / (1.0 - half_width)
            per_bin = 1.0 + (float(bin_step) / 10000.0)
            if per_bin <= 1.0:
                return None
            return int(math.ceil(math.log(ratio) / math.log(per_bin)))
        except Exception:
            return None

    def half_width_for_bins(self, bin_step: int, bins: int) -> Optional[Decimal]:
        """
        反推给定 bin 数量对应的半宽度百分比（近似）。

        Args:
            bin_step: bin step（bps）。
            bins: bin 数量。

        Returns:
            半宽度百分比；无法计算时返回 None。
        """
        try:
            if bin_step <= 0 or bins <= 0:
                return None
            per_bin = 1.0 + (float(bin_step) / 10000.0)
            ratio = per_bin ** float(bins)
            half_width = (ratio - 1.0) / (ratio + 1.0)
            if half_width <= 0:
                return None
            return Decimal(str(half_width * 100.0))
        except Exception:
            return None

    def effective_half_width_pct(
        self,
        half_width_pct: Decimal,
        pool_info: Optional[Any],
        connector_name: Optional[str] = None,
    ) -> Decimal:
        """
        基于 Meteora bin 限制修正半宽度，避免 InvalidPositionWidth。

        Args:
            half_width_pct: 配置的半宽度百分比。
            pool_info: 池信息对象（需要包含 bin_step 字段）。
            connector_name: 连接器名称（用于判断是否为 DLMM）。

        Returns:
            实际使用的半宽度百分比。
        """
        if connector_name is not None and not self._is_dlmm_connector(connector_name):
            return half_width_pct
        if pool_info is None:
            return half_width_pct
        bin_step = getattr(pool_info, "bin_step", None)
        if not bin_step:
            return half_width_pct
        est_bins = self.estimate_bins_for_half_width_pct(half_width_pct, int(bin_step))
        if est_bins is None or est_bins <= self._max_bins:
            return half_width_pct
        clamped = self.half_width_for_bins(int(bin_step), self._max_bins)
        if clamped is None or clamped <= 0:
            return half_width_pct
        self._log(
            logging.WARNING,
            f"Position width too wide (estimated_bins={est_bins} > {self._max_bins}). "
            f"Clamping half-width from {half_width_pct}% to {clamped}%.",
        )
        return clamped
