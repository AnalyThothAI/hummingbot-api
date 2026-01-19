"""
资金/预算管理模块，统一预算锚定、总预算上限、钱包边界与自动换仓逻辑。
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable, Optional, Tuple

from .lp_budget_checker import LPBudgetChecker
from .lp_position_manager import PortfolioStatus, PortfolioSummary

LogFunc = Callable[[int, str], None]
GetBalancesFunc = Callable[[], Awaitable[Tuple[Decimal, Decimal]]]
SwapFunc = Callable[[bool, Decimal, str, bool], Awaitable[bool]]
SwapByQuoteFunc = Callable[[Decimal, str, bool], Awaitable[bool]]


@dataclass
class BudgetSettings:
    """
    预算相关配置集合。

    Attributes:
        quote_floor: 额外保留的报价币底线（0 表示不保留）。
        budget_max_wallet_pct: 预算不超过钱包价值的比例上限。
        auto_swap_enabled: 是否允许自动换仓平衡预算。
        target_base_value_pct: 目标 base 价值占比（0-1）。
        swap_min_quote_value: 最小换仓报价币价值阈值。
        swap_safety_buffer_pct: 换仓安全缓冲比例（避免余额不足）。
        gas_token_symbol: Gas 代币符号（例如 SOL/ETH/BNB）。
        gas_min_reserve: Gas 代币最低预留数量。
        max_active_positions: 允许的活跃仓位数量上限（0 表示必须为空仓）。
        all_or_none: 是否启用严格预算模式（余额不足则拒绝）。
    """

    quote_floor: Decimal
    budget_max_wallet_pct: Decimal
    auto_swap_enabled: bool
    target_base_value_pct: Decimal
    swap_min_quote_value: Decimal
    swap_safety_buffer_pct: Decimal
    gas_token_symbol: str
    gas_min_reserve: Decimal
    max_active_positions: int = 0
    all_or_none: bool = True


@dataclass
class BudgetPlan:
    """
    预算规划结果。

    Attributes:
        base_budget: 实际 base 预算。
        quote_budget: 实际 quote 预算。
        allowed: 是否允许开仓。
        reason: 决策原因。
        lock_id: 预算锁 ID（可选）。
        locked_base: 锁定的 base 数量。
        locked_quote: 锁定的 quote 数量。
    """

    base_budget: Decimal
    quote_budget: Decimal
    allowed: bool
    reason: str
    lock_id: Optional[str] = None
    locked_base: Decimal = Decimal("0")
    locked_quote: Decimal = Decimal("0")


class BudgetManager:
    """
    预算管理器，负责预算锚定、总预算上限、钱包边界与自动换仓。
    """

    def __init__(
        self,
        settings: BudgetSettings,
        base_token: str,
        quote_token: str,
        get_balances: GetBalancesFunc,
        swap_func: Optional[SwapFunc] = None,
        swap_by_quote_func: Optional[SwapByQuoteFunc] = None,
        log_func: Optional[LogFunc] = None,
    ) -> None:
        """
        初始化预算管理器。

        Args:
            settings: 预算配置。
            base_token: base 币种符号（用于 gas 预留判断）。
            quote_token: 报价币符号（用于 gas 预留判断）。
            get_balances: 获取钱包余额的异步函数，返回 (base, quote)。
            swap_func: 执行换仓的异步函数（可选，按 base 数量）。
            swap_by_quote_func: 执行换仓的异步函数（可选，按 quote 数量，用于精确输入）。
            log_func: 日志函数（可选，签名为 (level, message)）。
        """
        self._settings = settings
        self._base_token = base_token
        self._quote_token = quote_token
        self._get_balances = get_balances
        self._swap_func = swap_func
        self._swap_by_quote_func = swap_by_quote_func
        self._log = log_func or self._default_log

        self._anchor_value_quote: Optional[Decimal] = None
        self._budget_checker = LPBudgetChecker(log_func=self._log)
        self._ledger_initialized: bool = False
        self._ledger_wallet_base: Decimal = Decimal("0")
        self._ledger_wallet_quote: Decimal = Decimal("0")
        self._ledger_deployed_base: Decimal = Decimal("0")
        self._ledger_deployed_quote: Decimal = Decimal("0")
        self._config_base_amount: Decimal = Decimal("0")
        self._config_quote_amount: Decimal = Decimal("0")
        self._gas_symbol_warned: bool = False

    def _ensure_ledger_initialized(self, base_amount: Decimal, quote_amount: Decimal) -> None:
        """
        初始化预算账本（仅首次或手动重置时）。

        Args:
            base_amount: 初始 base 预算。
            quote_amount: 初始 quote 预算。
        """
        if self._ledger_initialized:
            return
        self._ledger_wallet_base = max(Decimal("0"), base_amount)
        self._ledger_wallet_quote = max(Decimal("0"), quote_amount)
        self._ledger_deployed_base = Decimal("0")
        self._ledger_deployed_quote = Decimal("0")
        self._ledger_initialized = True

    def _ledger_cap_value(self, fallback_value: Decimal) -> Decimal:
        """
        获取预算账本的上限价值。

        Args:
            fallback_value: 当锚定值为空时使用的 fallback 值。

        Returns:
            预算上限价值（报价币）。
        """
        if self._anchor_value_quote is None:
            return fallback_value
        return min(fallback_value, self._anchor_value_quote)

    def _ledger_total_value(self, price: Decimal) -> Decimal:
        """
        计算预算账本总价值（钱包 + 已部署）。

        Args:
            price: 当前价格。

        Returns:
            预算账本总价值（报价币）。
        """
        if price <= 0:
            return Decimal("0")
        total_base = self._ledger_wallet_base + self._ledger_deployed_base
        total_quote = self._ledger_wallet_quote + self._ledger_deployed_quote
        return total_base * price + total_quote

    def ledger_snapshot(self, price: Decimal) -> dict:
        """
        获取预算账本快照。

        Args:
            price: 当前价格。

        Returns:
            包含钱包/部署数量与总价值的快照字典。
        """
        total_value = None
        if price > 0:
            total_value = self._ledger_total_value(price)
        return {
            "wallet_base": self._ledger_wallet_base,
            "wallet_quote": self._ledger_wallet_quote,
            "deployed_base": self._ledger_deployed_base,
            "deployed_quote": self._ledger_deployed_quote,
            "total_value_quote": total_value,
            "anchor_value_quote": self._anchor_value_quote,
        }

    def _apply_ledger_cap(self, price: Decimal, fallback_value: Decimal) -> None:
        """
        将预算账本限制在锚定上限内（超额部分视为非预算资产）。

        Args:
            price: 当前价格。
            fallback_value: 锚定缺失时的预算上限。
        """
        if not self._ledger_initialized or price <= 0:
            return
        cap_value = self._ledger_cap_value(fallback_value)
        deployed_value = self._ledger_deployed_base * price + self._ledger_deployed_quote
        cap_value = max(cap_value, deployed_value)
        total_value = self._ledger_total_value(price)
        if total_value <= cap_value:
            return
        excess_value = total_value - cap_value
        quote_reduction = min(self._ledger_wallet_quote, excess_value)
        self._ledger_wallet_quote -= quote_reduction
        excess_value -= quote_reduction
        if excess_value > 0 and price > 0:
            base_reduction = min(self._ledger_wallet_base, excess_value / price)
            self._ledger_wallet_base -= base_reduction

    def record_open(self, base_amount: Decimal, quote_amount: Decimal, price: Decimal) -> None:
        """
        记录开仓部署的预算金额。

        Args:
            base_amount: 开仓 base 数量。
            quote_amount: 开仓 quote 数量。
            price: 当前价格（用于账本上限裁剪）。
        """
        self._ensure_ledger_initialized(self._config_base_amount, self._config_quote_amount)
        if base_amount <= 0 and quote_amount <= 0:
            return
        if base_amount > self._ledger_wallet_base or quote_amount > self._ledger_wallet_quote:
            self._log(
                logging.WARNING,
                "Ledger wallet insufficient on open. "
                f"need_base={base_amount} need_quote={quote_amount} "
                f"ledger_base={self._ledger_wallet_base} ledger_quote={self._ledger_wallet_quote}",
            )
        self._ledger_wallet_base = max(Decimal("0"), self._ledger_wallet_base - base_amount)
        self._ledger_wallet_quote = max(Decimal("0"), self._ledger_wallet_quote - quote_amount)
        self._ledger_deployed_base += base_amount
        self._ledger_deployed_quote += quote_amount
        fallback_value = (self._config_base_amount * price) + self._config_quote_amount
        self._apply_ledger_cap(price, fallback_value)

    def record_close(self, base_amount: Decimal, quote_amount: Decimal, price: Decimal) -> None:
        """
        记录关仓返回的预算金额。

        Args:
            base_amount: 返还 base 数量。
            quote_amount: 返还 quote 数量。
            price: 当前价格（用于账本上限裁剪）。
        """
        self._ensure_ledger_initialized(self._config_base_amount, self._config_quote_amount)
        if base_amount <= 0 and quote_amount <= 0:
            return
        self._ledger_deployed_base = max(Decimal("0"), self._ledger_deployed_base - base_amount)
        self._ledger_deployed_quote = max(Decimal("0"), self._ledger_deployed_quote - quote_amount)
        self._ledger_wallet_base += base_amount
        self._ledger_wallet_quote += quote_amount
        fallback_value = (self._config_base_amount * price) + self._config_quote_amount
        self._apply_ledger_cap(price, fallback_value)

    def record_swap_delta(self, base_delta: Decimal, quote_delta: Decimal, price: Decimal) -> None:
        """
        记录预算内换仓带来的钱包变动。

        Args:
            base_delta: base 增量（正为增加）。
            quote_delta: quote 增量（正为增加）。
            price: 当前价格（用于账本上限裁剪）。
        """
        self._ensure_ledger_initialized(self._config_base_amount, self._config_quote_amount)
        if base_delta == 0 and quote_delta == 0:
            return
        self._ledger_wallet_base = max(Decimal("0"), self._ledger_wallet_base + base_delta)
        self._ledger_wallet_quote = max(Decimal("0"), self._ledger_wallet_quote + quote_delta)
        fallback_value = (self._config_base_amount * price) + self._config_quote_amount
        self._apply_ledger_cap(price, fallback_value)

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数，使用模块 logger 输出。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    def _normalize_symbol(self, symbol: str) -> str:
        """
        规范化币种符号。

        Args:
            symbol: 币种符号。

        Returns:
            大写后的币种符号。
        """
        return (symbol or "").upper().strip()

    def _wallet_available_balances(
        self, wallet_base: Decimal, wallet_quote: Decimal
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """
        计算扣除 reserve 后的可用余额。

        Args:
            wallet_base: base 余额。
            wallet_quote: quote 余额。

        Returns:
            (base_avail, quote_avail, quote_floor)。
        """
        quote_floor = max(Decimal("0"), self._settings.quote_floor)
        gas_symbol = self._normalize_symbol(self._settings.gas_token_symbol)
        gas_reserve = max(Decimal("0"), self._settings.gas_min_reserve)

        base_avail = wallet_base
        if gas_symbol and gas_reserve > 0:
            base_symbol = self._normalize_symbol(self._base_token)
            quote_symbol = self._normalize_symbol(self._quote_token)
            if gas_symbol == base_symbol:
                base_avail = max(Decimal("0"), wallet_base - gas_reserve)
            elif gas_symbol == quote_symbol:
                quote_floor = max(quote_floor, gas_reserve)
            else:
                if not self._gas_symbol_warned:
                    self._log(
                        logging.WARNING,
                        f"Gas reserve token {gas_symbol} not in pair {base_symbol}-{quote_symbol}; "
                        "reserve cannot be enforced.",
                    )
                    self._gas_symbol_warned = True

        quote_avail = max(Decimal("0"), wallet_quote - quote_floor)
        return base_avail, quote_avail, quote_floor

    def effective_quote_floor(self) -> Decimal:
        """
        计算当前配置下的有效 quote 预留底线（包含 gas 预留影响）。

        Returns:
            有效 quote 预留值。
        """
        quote_floor = max(Decimal("0"), self._settings.quote_floor)
        gas_symbol = self._normalize_symbol(self._settings.gas_token_symbol)
        gas_reserve = max(Decimal("0"), self._settings.gas_min_reserve)
        quote_symbol = self._normalize_symbol(self._quote_token)
        if gas_symbol and gas_symbol == quote_symbol:
            quote_floor = max(quote_floor, gas_reserve)
        return quote_floor

    @property
    def anchor_value_quote(self) -> Optional[Decimal]:
        """
        获取预算锚定值（报价币计价）。

        Returns:
            预算锚定值；未设置时为 None。
        """
        return self._anchor_value_quote

    def record_anchor(self, value_quote: Optional[Decimal]) -> None:
        """
        记录预算锚定值（仅首次记录）。

        Args:
            value_quote: 报价币计价的预算价值。
        """
        if self._anchor_value_quote is None and value_quote is not None:
            self._anchor_value_quote = value_quote

    def set_budget_amounts(self, base_amount: Decimal, quote_amount: Decimal) -> None:
        """
        同步配置预算金额到内部账本。

        Args:
            base_amount: 配置的 base 预算。
            quote_amount: 配置的 quote 预算。
        """
        sanitized_base = max(Decimal("0"), base_amount)
        sanitized_quote = max(Decimal("0"), quote_amount)
        self._config_base_amount = sanitized_base
        self._config_quote_amount = sanitized_quote
        self._ensure_ledger_initialized(sanitized_base, sanitized_quote)

    def commit_lock(self, lock_id: Optional[str]) -> None:
        """
        提交预算锁（开仓确认后释放锁定）。

        Args:
            lock_id: 预算锁 ID。
        """
        if not lock_id:
            return
        self._budget_checker.release_lock(lock_id, reason="commit")

    def release_lock(self, lock_id: Optional[str], reason: str) -> None:
        """
        释放预算锁（开仓失败或超时）。

        Args:
            lock_id: 预算锁 ID。
            reason: 释放原因。
        """
        if not lock_id:
            return
        self._budget_checker.release_lock(lock_id, reason=reason)

    def budget_value_in_quote(self, base_budget: Decimal, quote_budget: Decimal, price: Decimal) -> Decimal:
        """
        计算预算总价值（报价币计价）。

        Args:
            base_budget: base 预算。
            quote_budget: quote 预算。
            price: 当前价格。

        Returns:
            预算总价值。
        """
        return (base_budget * price) + quote_budget

    def apply_wallet_caps(
        self,
        base_budget: Decimal,
        quote_budget: Decimal,
        price: Decimal,
        wallet_base: Decimal,
        quote_avail: Decimal,
    ) -> Tuple[Decimal, Decimal]:
        """
        根据钱包规模与预算上限比例，缩放开仓预算。

        Args:
            base_budget: base 预算。
            quote_budget: quote 预算。
            price: 当前价格。
            wallet_base: 钱包 base 余额。
            quote_avail: 可用 quote 余额（已扣除保底）。

        Returns:
            缩放后的 (base_budget, quote_budget)。
        """
        max_pct = self._settings.budget_max_wallet_pct
        if max_pct is None or max_pct <= 0:
            return base_budget, quote_budget
        wallet_value = (wallet_base * price) + quote_avail
        if wallet_value <= 0:
            return Decimal("0"), Decimal("0")
        budget_value = self.budget_value_in_quote(base_budget, quote_budget, price)
        max_value = wallet_value * max_pct
        if budget_value <= max_value or budget_value <= 0:
            return base_budget, quote_budget
        scale = max_value / budget_value
        scaled_base = base_budget * scale
        scaled_quote = quote_budget * scale
        self._log(
            logging.INFO,
            f"Budget scaled by wallet cap: scale={scale:.4f} budget_value={budget_value:.6f} max_value={max_value:.6f}",
        )
        return scaled_base, scaled_quote

    def balanced_targets_from_budget(
        self, base_budget: Decimal, quote_budget: Decimal, price: Decimal
    ) -> Tuple[Decimal, Decimal]:
        """
        根据预算与目标比例计算 base/quote 目标投入值。

        Args:
            base_budget: base 预算。
            quote_budget: quote 预算。
            price: 当前价格。

        Returns:
            目标 (base, quote) 数量。
        """
        total_value_quote = base_budget * price + quote_budget
        if total_value_quote <= 0:
            return base_budget, quote_budget
        base_value_target = total_value_quote * self._settings.target_base_value_pct
        quote_value_target = total_value_quote - base_value_target
        base_target = base_value_target / price if price > 0 else Decimal("0")
        quote_target = quote_value_target
        return base_target, quote_target

    async def plan_open(
        self,
        base_amt: Decimal,
        quote_amt: Decimal,
        price: Decimal,
        allow_auto_balance: bool,
        label: str,
        portfolio: PortfolioSummary,
    ) -> BudgetPlan:
        """
        规划开仓预算（基于总预算上限与已部署价值）。

        Args:
            base_amt: 配置的 base 预算。
            quote_amt: 配置的 quote 预算。
            price: 当前价格。
            allow_auto_balance: 是否允许自动换仓平衡预算。
            label: 日志标签。
            portfolio: 当前链上仓位汇总。

        Returns:
            预算规划结果。

        说明:
            当自动换仓失败时，会直接返回 auto_swap_failed，避免继续后续校验。
        """
        sanitized_base = max(Decimal("0"), base_amt)
        sanitized_quote = max(Decimal("0"), quote_amt)
        self.set_budget_amounts(sanitized_base, sanitized_quote)
        price_for_value = price if price > 0 else Decimal("0")
        target_value = (sanitized_base * price_for_value) + sanitized_quote

        wallet_base, wallet_quote = await self._get_balances()
        base_avail, quote_avail, _ = self._wallet_available_balances(wallet_base, wallet_quote)

        allowed = True
        reason = "ok"
        if price <= 0:
            allowed = False
            reason = "price_invalid"
        elif portfolio.status != PortfolioStatus.OK:
            allowed = False
            reason = f"portfolio_{portfolio.status.value}"
        elif portfolio.active_count > self._settings.max_active_positions:
            allowed = False
            reason = "active_position_exists"
        elif target_value <= 0:
            allowed = False
            reason = "target_value_zero"

        anchor_cap = target_value
        if self._anchor_value_quote is not None:
            anchor_cap = min(anchor_cap, self._anchor_value_quote)

        deployed_base = self._ledger_deployed_base
        deployed_quote = self._ledger_deployed_quote
        if price_for_value > 0:
            deployed_value = (deployed_base * price_for_value) + deployed_quote
        else:
            deployed_value = Decimal("0")
        remaining_value = max(Decimal("0"), anchor_cap - deployed_value) if target_value > 0 else Decimal("0")
        scale = (remaining_value / target_value) if target_value > 0 else Decimal("0")
        if price_for_value > 0:
            self._apply_ledger_cap(price_for_value, target_value)
        ledger_wallet_base = min(self._ledger_wallet_base, base_avail)
        ledger_wallet_quote = min(self._ledger_wallet_quote, quote_avail)
        budget_wallet_base, budget_wallet_quote = self._budget_checker.available(
            ledger_wallet_base, ledger_wallet_quote
        )

        if allowed and remaining_value <= 0:
            allowed = False
            reason = "budget_depleted"

        if not allowed:
            scale = Decimal("0")
        base_budget = sanitized_base * scale if allowed else Decimal("0")
        quote_budget = sanitized_quote * scale if allowed else Decimal("0")
        lock_id: Optional[str] = None
        locked_base = Decimal("0")
        locked_quote = Decimal("0")

        if allowed:
            if self._ledger_wallet_base > base_avail or self._ledger_wallet_quote > quote_avail:
                allowed = False
                reason = "wallet_out_of_budget"

            if base_budget > budget_wallet_base or quote_budget > budget_wallet_quote:
                self._log(
                    logging.WARNING,
                    f"Budget exceeds ledger wallet. base_budget={base_budget} ledger_base={budget_wallet_base}, "
                    f"quote_budget={quote_budget} ledger_quote={budget_wallet_quote}",
                )

            base_budget, quote_budget = self.apply_wallet_caps(
                base_budget=base_budget,
                quote_budget=quote_budget,
                price=price_for_value,
                wallet_base=budget_wallet_base,
                quote_avail=budget_wallet_quote,
            )

            if self._settings.auto_swap_enabled and allow_auto_balance:
                base_budget, quote_budget, swap_ok, swap_used = await self._auto_balance_with_swap(
                    base_budget=base_budget,
                    quote_budget=quote_budget,
                    price=price_for_value,
                    label=label,
                )
                if not swap_ok:
                    allowed = False
                    reason = "auto_swap_failed"
                    base_budget = Decimal("0")
                    quote_budget = Decimal("0")
                    plan = BudgetPlan(
                        base_budget=base_budget,
                        quote_budget=quote_budget,
                        allowed=allowed,
                        reason=reason,
                        lock_id=None,
                        locked_base=Decimal("0"),
                        locked_quote=Decimal("0"),
                    )
                    self._log(logging.WARNING, f"Budget plan blocked: reason={reason} label={label}")
                    return plan
                if swap_used:
                    wallet_base, wallet_quote = await self._get_balances()
                    base_avail, quote_avail, _ = self._wallet_available_balances(wallet_base, wallet_quote)
                    ledger_wallet_base = min(self._ledger_wallet_base, base_avail)
                    ledger_wallet_quote = min(self._ledger_wallet_quote, quote_avail)
                    budget_wallet_base, budget_wallet_quote = self._budget_checker.available(
                        ledger_wallet_base, ledger_wallet_quote
                    )
                if remaining_value > 0 and price_for_value > 0:
                    base_cap_value = remaining_value * self._settings.target_base_value_pct
                    base_cap = base_cap_value / price_for_value
                    quote_cap = remaining_value - base_cap_value
                    base_budget = min(base_budget, base_cap)
                    quote_budget = min(quote_budget, quote_cap)

            if self._settings.all_or_none:
                if base_budget > budget_wallet_base or quote_budget > budget_wallet_quote:
                    allowed = False
                    reason = "wallet_insufficient"
            else:
                base_budget = min(base_budget, budget_wallet_base)
                quote_budget = min(quote_budget, budget_wallet_quote)

            if remaining_value > 0 and price_for_value > 0:
                budget_value_used = (base_budget * price_for_value) + quote_budget
                if budget_value_used > remaining_value + Decimal("1e-12"):
                    allowed = False
                    reason = "budget_cap_exceeded"

            if base_budget <= 0 and quote_budget <= 0:
                allowed = False
                reason = "wallet_insufficient"
            if allowed:
                lock_result = self._budget_checker.lock(
                    base_amount=base_budget,
                    quote_amount=quote_budget,
                    wallet_base=ledger_wallet_base,
                    wallet_quote=ledger_wallet_quote,
                    all_or_none=self._settings.all_or_none,
                    label=label,
                )
                if not lock_result.allowed:
                    allowed = False
                    reason = f"lock_failed:{lock_result.reason}"
                    base_budget = Decimal("0")
                    quote_budget = Decimal("0")
                else:
                    lock_id = lock_result.lock_id
                    locked_base = lock_result.base_amount
                    locked_quote = lock_result.quote_amount
                    base_budget = lock_result.base_amount
                    quote_budget = lock_result.quote_amount
                    if lock_result.resized:
                        reason = "lock_scaled"
            if not allowed:
                base_budget = Decimal("0")
                quote_budget = Decimal("0")

        plan = BudgetPlan(
            base_budget=base_budget,
            quote_budget=quote_budget,
            allowed=allowed,
            reason=reason,
            lock_id=lock_id,
            locked_base=locked_base,
            locked_quote=locked_quote,
        )

        if not allowed:
            self._log(logging.WARNING, f"Budget plan blocked: reason={reason} label={label}")
        return plan

    async def _auto_balance_with_swap(
        self, base_budget: Decimal, quote_budget: Decimal, price: Decimal, label: str
    ) -> Tuple[Decimal, Decimal, bool, bool]:
        """
        使用 swap 函数在 base/quote 间换仓，使部署比例接近 target_base_value_pct。

        Args:
            base_budget: base 预算。
            quote_budget: quote 预算。
            price: 当前价格。
            label: 日志标签。

        Returns:
            (base_budget, quote_budget, swap_ok, swap_used)。

        说明:
            若提供按 quote 输入的换仓函数，会优先使用该路径进行买入换仓。
        """
        if self._swap_func is None and self._swap_by_quote_func is None:
            return base_budget, quote_budget, True, False
        if base_budget < 0:
            base_budget = Decimal("0")
        if quote_budget < 0:
            quote_budget = Decimal("0")
        if base_budget == 0 and quote_budget == 0:
            return base_budget, quote_budget, True, False

        wallet_base, wallet_quote = await self._get_balances()
        base_avail, quote_avail, _ = self._wallet_available_balances(wallet_base, wallet_quote)
        ledger_wallet_base = min(self._ledger_wallet_base, base_avail)
        ledger_wallet_quote = min(self._ledger_wallet_quote, quote_avail)
        budget_wallet_base, budget_wallet_quote = self._budget_checker.available(
            ledger_wallet_base, ledger_wallet_quote
        )

        budget_value = base_budget * price + quote_budget
        if budget_value <= 0:
            return base_budget, quote_budget, True, False

        base_target, quote_target = self.balanced_targets_from_budget(base_budget, quote_budget, price)
        if base_target <= 0 or quote_target <= 0:
            return base_budget, quote_budget, True, False

        swap_used = False
        base_deficit = max(Decimal("0"), base_target - budget_wallet_base)
        quote_deficit = max(Decimal("0"), quote_target - budget_wallet_quote)

        if base_deficit > 0 and budget_wallet_quote > quote_target:
            quote_to_convert = min(budget_wallet_quote - quote_target, base_deficit * price)
            if quote_to_convert >= self._settings.swap_min_quote_value:
                swap_used = True
                before_base = wallet_base
                before_quote = wallet_quote
                if self._swap_by_quote_func is not None:
                    ok = await self._swap_by_quote_func(quote_to_convert, label, False)
                else:
                    buy_base_amt = (quote_to_convert / price) * (
                        Decimal("1") - self._settings.swap_safety_buffer_pct / Decimal("100")
                    )
                    buy_base_amt = max(Decimal("0"), buy_base_amt)
                    ok = await self._swap_func(True, buy_base_amt, label, False)
                if not ok:
                    self._log(
                        logging.ERROR,
                        "Auto-swap BUY failed: "
                        f"label={label} quote_to_convert={quote_to_convert} "
                        f"wallet_base={wallet_base} wallet_quote_avail={quote_avail} "
                        f"base_target={base_target} quote_target={quote_target}",
                    )
                    return Decimal("0"), Decimal("0"), False, swap_used
                wallet_base, wallet_quote = await self._get_balances()
                base_avail, quote_avail, _ = self._wallet_available_balances(wallet_base, wallet_quote)
                self.record_swap_delta(wallet_base - before_base, wallet_quote - before_quote, price)
                ledger_wallet_base = min(self._ledger_wallet_base, base_avail)
                ledger_wallet_quote = min(self._ledger_wallet_quote, quote_avail)
                available_base, available_quote = self._budget_checker.available(
                    ledger_wallet_base, ledger_wallet_quote
                )
                base_budget = min(base_target, available_base)
                quote_budget = min(quote_target, available_quote)
                return base_budget, quote_budget, True, swap_used

        if quote_deficit > 0 and budget_wallet_base > base_target:
            quote_needed = quote_deficit
            if quote_needed >= self._settings.swap_min_quote_value:
                sell_base_amt = (quote_needed / price) * (
                    Decimal("1") + self._settings.swap_safety_buffer_pct / Decimal("100")
                )
                sell_base_amt = min(sell_base_amt, budget_wallet_base - base_target)
                sell_base_amt = max(Decimal("0"), sell_base_amt)
                if sell_base_amt > 0:
                    swap_used = True
                    before_base = wallet_base
                    before_quote = wallet_quote
                    ok = await self._swap_func(False, sell_base_amt, label, False)
                    if not ok:
                        self._log(
                            logging.ERROR,
                            "Auto-swap SELL failed: "
                            f"label={label} sell_base_amt={sell_base_amt} "
                            f"wallet_base={wallet_base} wallet_quote_avail={quote_avail} "
                            f"base_target={base_target} quote_target={quote_target}",
                        )
                        return Decimal("0"), Decimal("0"), False, swap_used
                    wallet_base, wallet_quote = await self._get_balances()
                    base_avail, quote_avail, _ = self._wallet_available_balances(wallet_base, wallet_quote)
                    self.record_swap_delta(wallet_base - before_base, wallet_quote - before_quote, price)
                    ledger_wallet_base = min(self._ledger_wallet_base, base_avail)
                    ledger_wallet_quote = min(self._ledger_wallet_quote, quote_avail)
                    available_base, available_quote = self._budget_checker.available(
                        ledger_wallet_base, ledger_wallet_quote
                    )
                    base_budget = min(base_target, available_base)
                    quote_budget = min(quote_target, available_quote)
                    return base_budget, quote_budget, True, swap_used

        base_budget = min(base_target, budget_wallet_base)
        quote_budget = min(quote_target, budget_wallet_quote)
        return base_budget, quote_budget, True, swap_used
