"""
LP 状态格式化工具（表格化展示）。
"""

from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import tabulate
except Exception:  # pragma: no cover - fallback if tabulate unavailable
    tabulate = None


def to_decimal(value: Optional[object]) -> Optional[Decimal]:
    """
    将输入安全转换为 Decimal。

    Args:
        value: 输入值。

    Returns:
        Decimal 或 None。
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def range_deviation_pct(price: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    """
    计算区间偏离百分比。

    Args:
        price: 当前价格。
        lower: 下边界。
        upper: 上边界。

    Returns:
        偏离百分比。
    """
    if price < lower:
        return (lower - price) / lower * Decimal("100")
    if price > upper:
        return (price - upper) / upper * Decimal("100")
    return Decimal("0")


def format_kv_table(
    rows: Iterable[Tuple[str, str, Optional[str]]],
    indent: int = 2,
) -> List[str]:
    """
    将多分组 key/value 行格式化为单一 psql 表格。

    Args:
        rows: (section, field, value) 记录；value 为 None 时跳过。
        indent: 左侧缩进空格数。

    Returns:
        格式化后的文本行列表。
    """
    cleaned: List[Tuple[str, str, str]] = []
    for section, field, value in rows:
        if value is None or value == "":
            continue
        cleaned.append((str(section), str(field), str(value)))
    if not cleaned:
        return []
    pad = " " * max(0, int(indent))
    if tabulate is None:
        section_width = max(len(section) for section, _, _ in cleaned)
        field_width = max(len(field) for _, field, _ in cleaned)
        lines = []
        for section, field, value in cleaned:
            lines.append(f"{pad}{section:<{section_width}}  {field:<{field_width}}  {value}")
        return lines
    table = tabulate.tabulate(
        cleaned,
        headers=["section", "field", "value"],
        tablefmt="psql",
        colalign=("left", "left", "left"),
    )
    return [f"{pad}{line}" for line in table.splitlines()]


def format_lp_status_lines(
    ci: Dict[str, object],
    base_token: str,
    quote_token: str,
    max_rebalances_per_hour: int,
) -> List[str]:
    """
    构建 LP 状态的表格化展示内容。

    Args:
        ci: 执行器 custom info。
        base_token: base 代币符号。
        quote_token: quote 代币符号。
        max_rebalances_per_hour: 每小时最大再平衡次数（用于展示）。

    Returns:
        格式化后的文本行列表。
    """
    rows: List[Tuple[str, str, Optional[str]]] = []

    def add_row(section: str, field: str, value: Optional[str]) -> None:
        if value is None or value == "":
            return
        rows.append((section, field, value))

    pending_action = ci.get("pending_action") or "none"
    pending_age = ci.get("pending_action_age_sec")
    if pending_age is not None:
        pending_display = f"{pending_action} ({pending_age}s)"
    else:
        pending_display = pending_action

    add_row("status", "state", ci.get("lp_state"))
    add_row("status", "pending", pending_display)
    add_row("status", "swap", "yes" if ci.get("swap_in_progress") else "no")
    add_row("status", "last_action", ci.get("last_action") or "n/a")
    pause_left = ci.get("pause_left_sec")
    if pause_left and int(pause_left) > 0:
        add_row("status", "paused", f"{pause_left}s")
    last_error = ci.get("last_error")
    if last_error:
        add_row("status", "last_error", last_error)

    price = to_decimal(ci.get("price"))
    price_source = ci.get("price_source") or "pool_info"
    price_age = ci.get("price_age_sec")
    price_label = None
    if price is not None:
        extras = [price_source]
        if price_age is not None:
            extras.append(f"age={price_age}s")
        price_label = f"{price} ({', '.join(extras)})"

    entry_price = to_decimal(ci.get("entry_price"))
    entry_age = ci.get("entry_age_sec")
    entry_label = None
    if price is not None and entry_price is not None and entry_price > 0:
        delta_pct = (price - entry_price) / entry_price * Decimal("100")
        extras = [f"delta={delta_pct:+.2f}%"]
        if entry_age is not None:
            extras.append(f"age={entry_age}s")
        entry_label = f"{entry_price} ({', '.join(extras)})"

    lower = to_decimal(ci.get("range_lower"))
    upper = to_decimal(ci.get("range_upper"))
    range_label = None
    if price is not None and lower is not None and upper is not None:
        if price < lower:
            side = "BELOW"
        elif price > upper:
            side = "ABOVE"
        else:
            side = "IN_RANGE"
        deviation = range_deviation_pct(price, lower, upper)
        range_label = f"[{lower}, {upper}] side={side} dev={deviation:.2f}%"

    add_row("market", "price", price_label)
    add_row("market", "entry_price", entry_label)
    add_row("market", "range", range_label)
    add_row("market", "position_id", ci.get("position_id"))

    pos_base = to_decimal(ci.get("position_base"))
    pos_quote = to_decimal(ci.get("position_quote"))
    pos_total_base = to_decimal(ci.get("position_total_base"))
    pos_total_quote = to_decimal(ci.get("position_total_quote"))
    pos_fee_base = to_decimal(ci.get("position_fee_base")) or Decimal("0")
    pos_fee_quote = to_decimal(ci.get("position_fee_quote")) or Decimal("0")
    if pos_base is not None or pos_quote is not None:
        base_val = pos_base or Decimal("0")
        quote_val = pos_quote or Decimal("0")
        total_base = pos_total_base or base_val
        total_quote = pos_total_quote or quote_val
        add_row("position", "base", f"{base_val} {base_token}")
        add_row("position", "quote", f"{quote_val} {quote_token}")
        add_row("position", "total", f"{total_base} {base_token} / {total_quote} {quote_token}")
        add_row("position", "fees", f"{pos_fee_base} {base_token} / {pos_fee_quote} {quote_token}")

    entry_value = to_decimal(ci.get("entry_value_quote"))
    current_value = to_decimal(ci.get("current_value_quote"))
    total_pnl = to_decimal(ci.get("total_pnl_quote"))
    realized_pnl = to_decimal(ci.get("realized_pnl_quote"))
    unrealized_pnl = to_decimal(ci.get("unrealized_pnl_quote"))
    anchor_value = to_decimal(ci.get("anchor_value_quote"))
    net_pnl_pct = to_decimal(ci.get("net_pnl_pct"))
    if entry_value is not None:
        add_row("pnl", "entry_value", f"{entry_value} {quote_token}")
    if current_value is not None:
        add_row("pnl", "value", f"{current_value} {quote_token}")
    if total_pnl is not None:
        add_row("pnl", "pnl", f"{total_pnl} {quote_token}")
    if realized_pnl is not None or unrealized_pnl is not None:
        add_row(
            "pnl",
            "pnl_breakdown",
            f"realized={realized_pnl or Decimal('0')} unrealized={unrealized_pnl or Decimal('0')}",
        )
    if net_pnl_pct is not None:
        anchor_label = f"anchor={anchor_value} {quote_token}" if anchor_value is not None else None
        net_label = f"{net_pnl_pct * Decimal('100'):.2f}%"
        if anchor_label:
            net_label = f"{net_label} ({anchor_label})"
        add_row("pnl", "net_pnl", net_label)

    stop_loss_pct = to_decimal(ci.get("stop_loss_pnl_pct"))
    if stop_loss_pct is not None and stop_loss_pct > 0:
        trigger_pnl = ci.get("stop_loss_trigger_pnl")
        distance_pnl = ci.get("stop_loss_distance_pnl")
        display_pct = stop_loss_pct * Decimal("100")
        stop_loss_label = f"{display_pct:.2f}%"
        if trigger_pnl is not None:
            stop_loss_label += f" trigger={trigger_pnl}"
        if distance_pnl is not None:
            stop_loss_label += f" distance={distance_pnl}"
        add_row("stop_loss", "pnl", stop_loss_label)

    budget_total = to_decimal(ci.get("budget_total_value_quote"))
    add_row("budget", "cfg_base", f"{ci.get('budget_base')} {base_token}")
    add_row("budget", "cfg_quote", f"{ci.get('budget_quote')} {quote_token}")
    add_row(
        "budget",
        "wallet",
        f"{ci.get('budget_wallet_base')} {base_token} / {ci.get('budget_wallet_quote')} {quote_token}",
    )
    add_row(
        "budget",
        "deployed",
        f"{ci.get('budget_deployed_base')} {base_token} / {ci.get('budget_deployed_quote')} {quote_token}",
    )
    if budget_total is not None:
        add_row("budget", "total", f"{budget_total} {quote_token}")
    quote_floor = to_decimal(ci.get("budget_quote_floor"))
    config_floor = to_decimal(ci.get("budget_config_quote_floor"))
    gas_symbol = ci.get("gas_token_symbol") or quote_token
    gas_min = to_decimal(ci.get("gas_min_reserve"))
    if quote_floor is not None:
        reserve_label = f"quote_floor={quote_floor} {quote_token}"
        if gas_min is not None:
            reserve_label += f", gas_min={gas_min} {gas_symbol}"
        if config_floor is not None and quote_floor != config_floor:
            reserve_label += f" (cfg={config_floor})"
        add_row("budget", "reserve", reserve_label)

    rebalance_out = ci.get("rebalance_out_of_bounds_for_sec")
    rebalance_cd = ci.get("rebalance_cooldown_left_sec")
    rebalance_count = ci.get("rebalance_count_last_hour")
    rebalance_last_age = ci.get("rebalance_last_rebalance_age_sec")
    if rebalance_out is not None or rebalance_cd is not None or rebalance_count is not None:
        add_row("rebalance", "out_for", f"{rebalance_out or 0}s")
        add_row("rebalance", "cooldown_left", f"{rebalance_cd or 0}s")
        if max_rebalances_per_hour is None:
            limit_label = "n/a"
        elif max_rebalances_per_hour <= 0:
            limit_label = "disabled"
        else:
            limit_label = str(max_rebalances_per_hour)
        add_row("rebalance", "count_1h", f"{rebalance_count or 0}/{limit_label}")
    if rebalance_last_age is not None:
        add_row("rebalance", "last_rebalance_age", f"{rebalance_last_age}s")

    fee_value = to_decimal(ci.get("pending_fees_value_quote")) or Decimal("0")
    fee_base = to_decimal(ci.get("pending_fees_base")) or Decimal("0")
    fee_quote = to_decimal(ci.get("pending_fees_quote")) or Decimal("0")
    if fee_value > 0 or fee_base > 0 or fee_quote > 0:
        last_collect_age = ci.get("last_collect_fees_age_sec")
        to_quote = "yes" if ci.get("collect_fees_to_quote") else "no"
        min_fee_swap = ci.get("collect_fees_swap_min_quote_value")
        min_collect = ci.get("collect_fees_min_quote_value")
        add_row("fees", "pending", f"{fee_base} {base_token} / {fee_quote} {quote_token}")
        add_row("fees", "value", f"{fee_value} {quote_token}")
        add_row("fees", "last_collect", f"{last_collect_age or 'n/a'}s")
        add_row("fees", "to_quote", to_quote)
        add_row("fees", "min_collect", str(min_collect))
        add_row("fees", "min_swap", str(min_fee_swap))

    return format_kv_table(rows)
