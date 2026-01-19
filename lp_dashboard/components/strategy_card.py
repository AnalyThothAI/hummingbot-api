"""Strategy card component for LP Dashboard."""
import streamlit as st
from typing import Any, Callable, Dict, Optional
from ..api.models import StrategyInfo, BotStatus
from ..utils.helpers import format_pnl, format_number, get_pnl_color


def render_strategy_card(
    strategy: StrategyInfo,
    on_start: Optional[Callable] = None,
    on_stop: Optional[Callable] = None,
    on_view: Optional[Callable] = None,
    expanded: bool = False,
):
    """Render a strategy card with status and actions.

    Args:
        strategy: Strategy information
        on_start: Callback when start button is clicked
        on_stop: Callback when stop button is clicked
        on_view: Callback when view details button is clicked
        expanded: Whether to show expanded view
    """
    # Determine status indicator
    status_emoji = _get_status_emoji(strategy.status)
    status_color = _get_status_color(strategy.status)

    # Create the card container
    with st.container():
        # Header row
        col1, col2, col3 = st.columns([3, 1, 2])

        with col1:
            st.markdown(f"### {status_emoji} {strategy.name}")
            if strategy.trading_pair:
                st.caption(f"{strategy.exchange or 'Unknown'} | {strategy.trading_pair}")

        with col2:
            # Status badge
            st.markdown(
                f"<span style='background-color:{status_color};color:white;"
                f"padding:4px 8px;border-radius:4px;font-size:12px;'>"
                f"{strategy.status.value.upper()}</span>",
                unsafe_allow_html=True,
            )

        with col3:
            # Action buttons
            _render_action_buttons(strategy, on_start, on_stop, on_view)

        # Details section
        if expanded:
            _render_strategy_details(strategy)

        st.markdown("---")


def _get_status_emoji(status: BotStatus) -> str:
    """Get emoji for bot status."""
    emojis = {
        BotStatus.RUNNING: ":green_circle:",
        BotStatus.STOPPED: ":red_circle:",
        BotStatus.ERROR: ":x:",
        BotStatus.STARTING: ":hourglass_flowing_sand:",
        BotStatus.STOPPING: ":hourglass_flowing_sand:",
        BotStatus.UNKNOWN: ":question:",
    }
    return emojis.get(status, ":question:")


def _get_status_color(status: BotStatus) -> str:
    """Get color for bot status."""
    colors = {
        BotStatus.RUNNING: "#28a745",
        BotStatus.STOPPED: "#6c757d",
        BotStatus.ERROR: "#dc3545",
        BotStatus.STARTING: "#17a2b8",
        BotStatus.STOPPING: "#ffc107",
        BotStatus.UNKNOWN: "#6c757d",
    }
    return colors.get(status, "#6c757d")


def _render_action_buttons(
    strategy: StrategyInfo,
    on_start: Optional[Callable],
    on_stop: Optional[Callable],
    on_view: Optional[Callable],
):
    """Render action buttons for strategy card."""
    col1, col2, col3 = st.columns(3)

    with col1:
        # Start button (only show if stopped)
        if strategy.status in [BotStatus.STOPPED, BotStatus.ERROR]:
            if st.button(
                "Start",
                key=f"start_{strategy.name}",
                type="primary",
                use_container_width=True,
            ):
                if on_start:
                    on_start(strategy.name)

    with col2:
        # Stop button (only show if running)
        if strategy.status == BotStatus.RUNNING:
            if st.button(
                "Stop",
                key=f"stop_{strategy.name}",
                type="secondary",
                use_container_width=True,
            ):
                if on_stop:
                    on_stop(strategy.name)

    with col3:
        # View details button
        if st.button(
            "Details",
            key=f"view_{strategy.name}",
            use_container_width=True,
        ):
            if on_view:
                on_view(strategy.name)


def _render_strategy_details(strategy: StrategyInfo):
    """Render expanded strategy details."""
    with st.expander("Details", expanded=True):
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "PnL",
                format_pnl(strategy.pnl),
                delta_color="normal" if strategy.pnl and strategy.pnl >= 0 else "inverse",
            )

        with col2:
            st.metric("Fees Collected", format_number(strategy.fees_collected, prefix="$"))

        with col3:
            st.metric("Uptime", strategy.uptime or "N/A")

        with col4:
            st.metric("Container", strategy.container_name or "N/A")

        # Additional info
        if strategy.script:
            st.text(f"Script: {strategy.script}")
        if strategy.config:
            st.text(f"Config: {strategy.config}")


def render_strategy_list(
    strategies: list,
    on_start: Optional[Callable] = None,
    on_stop: Optional[Callable] = None,
    on_view: Optional[Callable] = None,
):
    """Render a list of strategy cards.

    Args:
        strategies: List of StrategyInfo objects
        on_start: Callback for start action
        on_stop: Callback for stop action
        on_view: Callback for view action
    """
    if not strategies:
        st.info("No strategies found.")
        return

    # Sort strategies: running first, then by name
    sorted_strategies = sorted(
        strategies,
        key=lambda s: (s.status != BotStatus.RUNNING, s.name),
    )

    for strategy in sorted_strategies:
        render_strategy_card(
            strategy=strategy,
            on_start=on_start,
            on_stop=on_stop,
            on_view=on_view,
            expanded=strategy.status == BotStatus.RUNNING,
        )


def render_compact_strategy_row(
    strategy: StrategyInfo,
    on_action: Optional[Callable] = None,
):
    """Render a compact strategy row for tables."""
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])

    with col1:
        status_emoji = _get_status_emoji(strategy.status)
        st.markdown(f"{status_emoji} **{strategy.name}**")

    with col2:
        st.text(strategy.trading_pair or "N/A")

    with col3:
        pnl_str = format_pnl(strategy.pnl)
        pnl_color = get_pnl_color(strategy.pnl)
        st.markdown(f"<span style='color:{pnl_color}'>{pnl_str}</span>", unsafe_allow_html=True)

    with col4:
        st.text(strategy.uptime or "N/A")

    with col5:
        action_label = "Stop" if strategy.status == BotStatus.RUNNING else "Start"
        if st.button(action_label, key=f"action_{strategy.name}"):
            if on_action:
                on_action(strategy.name, action_label.lower())
