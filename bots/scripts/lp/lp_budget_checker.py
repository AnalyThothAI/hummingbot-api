"""
LP 预算检查器：对预算进行 all_or_none 校验并提供锁定机制。
"""

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, Optional, Tuple

LogFunc = Callable[[int, str], None]


@dataclass
class LPBudgetLock:
    """
    预算锁信息。

    Attributes:
        lock_id: 锁 ID。
        base_amount: 锁定的 base 数量。
        quote_amount: 锁定的 quote 数量。
        created_ts: 创建时间戳。
        label: 锁标记（日志用）。
    """

    lock_id: str
    base_amount: Decimal
    quote_amount: Decimal
    created_ts: float
    label: str


@dataclass
class LPBudgetLockResult:
    """
    锁定结果。

    Attributes:
        allowed: 是否允许锁定。
        reason: 失败原因（allowed=False 时有效）。
        base_amount: 实际锁定 base 数量。
        quote_amount: 实际锁定 quote 数量。
        lock_id: 锁 ID（成功时有效）。
        resized: 是否发生缩放。
    """

    allowed: bool
    reason: str
    base_amount: Decimal
    quote_amount: Decimal
    lock_id: Optional[str] = None
    resized: bool = False


class LPBudgetChecker:
    """
    LP 操作专用预算检查器（借鉴官方 BudgetChecker）。
    """

    def __init__(self, log_func: Optional[LogFunc] = None) -> None:
        """
        初始化预算检查器。

        Args:
            log_func: 日志函数（可选）。
        """
        self._locked_base: Decimal = Decimal("0")
        self._locked_quote: Decimal = Decimal("0")
        self._locks: Dict[str, LPBudgetLock] = {}
        self._log = log_func or self._default_log

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    @property
    def locked_base(self) -> Decimal:
        """
        获取已锁定 base 数量。

        Returns:
            已锁定 base。
        """
        return self._locked_base

    @property
    def locked_quote(self) -> Decimal:
        """
        获取已锁定 quote 数量。

        Returns:
            已锁定 quote。
        """
        return self._locked_quote

    def available(self, wallet_base: Decimal, wallet_quote: Decimal) -> Tuple[Decimal, Decimal]:
        """
        计算扣除锁定后的可用余额。

        Args:
            wallet_base: 可用 base。
            wallet_quote: 可用 quote。

        Returns:
            (available_base, available_quote)。
        """
        available_base = max(Decimal("0"), wallet_base - self._locked_base)
        available_quote = max(Decimal("0"), wallet_quote - self._locked_quote)
        return available_base, available_quote

    def lock(
        self,
        base_amount: Decimal,
        quote_amount: Decimal,
        wallet_base: Decimal,
        wallet_quote: Decimal,
        all_or_none: bool,
        label: str,
    ) -> LPBudgetLockResult:
        """
        校验预算并锁定可用余额。

        Args:
            base_amount: 需要锁定的 base 数量。
            quote_amount: 需要锁定的 quote 数量。
            wallet_base: 可用 base。
            wallet_quote: 可用 quote。
            all_or_none: True 表示余额不足则拒绝；False 表示缩放。
            label: 锁标记。

        Returns:
            锁定结果。
        """
        if base_amount <= 0 and quote_amount <= 0:
            return LPBudgetLockResult(
                allowed=False,
                reason="zero_budget",
                base_amount=Decimal("0"),
                quote_amount=Decimal("0"),
            )

        available_base, available_quote = self.available(wallet_base, wallet_quote)
        if base_amount > available_base or quote_amount > available_quote:
            if all_or_none:
                return LPBudgetLockResult(
                    allowed=False,
                    reason="insufficient_available",
                    base_amount=Decimal("0"),
                    quote_amount=Decimal("0"),
                )
            scale_base = (available_base / base_amount) if base_amount > 0 else Decimal("1")
            scale_quote = (available_quote / quote_amount) if quote_amount > 0 else Decimal("1")
            scale = min(scale_base, scale_quote)
            if scale <= 0:
                return LPBudgetLockResult(
                    allowed=False,
                    reason="insufficient_available",
                    base_amount=Decimal("0"),
                    quote_amount=Decimal("0"),
                )
            base_amount *= scale
            quote_amount *= scale
            resized = True
        else:
            resized = False

        lock_id = uuid.uuid4().hex
        self._locks[lock_id] = LPBudgetLock(
            lock_id=lock_id,
            base_amount=base_amount,
            quote_amount=quote_amount,
            created_ts=time.time(),
            label=label,
        )
        self._locked_base += base_amount
        self._locked_quote += quote_amount
        return LPBudgetLockResult(
            allowed=True,
            reason="ok",
            base_amount=base_amount,
            quote_amount=quote_amount,
            lock_id=lock_id,
            resized=resized,
        )

    def release_lock(self, lock_id: str, reason: str = "") -> bool:
        """
        释放指定锁。

        Args:
            lock_id: 锁 ID。
            reason: 释放原因。

        Returns:
            是否成功释放。
        """
        lock = self._locks.pop(lock_id, None)
        if lock is None:
            return False
        self._locked_base = max(Decimal("0"), self._locked_base - lock.base_amount)
        self._locked_quote = max(Decimal("0"), self._locked_quote - lock.quote_amount)
        if reason:
            self._log(logging.INFO, f"Budget lock released: id={lock_id} reason={reason}")
        return True
