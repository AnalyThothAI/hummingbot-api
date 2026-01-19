"""Metric card components for LP Dashboard."""
import streamlit as st
from typing import Any, Optional, List, Dict
from .theme import get_color_scheme, THEME_CONFIG

colors = get_color_scheme()


def render_metric_card(
    label: str,
    value: str,
    delta: Optional[str] = None,
    delta_color: str = "normal",
    icon: Optional[str] = None,
    color: str = "primary",
) -> None:
    """Render a styled metric card.

    Args:
        label: Metric label/title
        value: Metric value to display
        delta: Optional delta/change value
        delta_color: "normal" (green=positive), "inverse" (red=positive), or color name
        icon: Optional emoji icon
        color: Card accent color (primary, secondary, warning, danger)
    """
    # Determine accent color
    accent_colors = {
        "primary": THEME_CONFIG["primary_color"],
        "secondary": THEME_CONFIG["secondary_color"],
        "warning": THEME_CONFIG["warning_color"],
        "danger": THEME_CONFIG["error_color"],
        "info": THEME_CONFIG["info_color"],
    }
    accent = accent_colors.get(color, THEME_CONFIG["primary_color"])

    # Determine delta styling
    delta_html = ""
    if delta:
        is_positive = delta.startswith("+") or (delta[0].isdigit() and not delta.startswith("-"))
        if delta_color == "normal":
            delta_class = "positive" if is_positive else "negative"
        elif delta_color == "inverse":
            delta_class = "negative" if is_positive else "positive"
        else:
            delta_class = delta_color

        delta_html = f'<div class="metric-delta {delta_class}">{delta}</div>'

    icon_html = f'<div class="metric-icon">{icon}</div>' if icon else ""

    html = f"""
    <div class="metric-card" style="border-top: 3px solid {accent};">
        {icon_html}
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {delta_html}
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def render_metric_row(metrics: List[Dict[str, Any]], columns: int = 4) -> None:
    """Render a row of metric cards.

    Args:
        metrics: List of metric dictionaries with keys: label, value, delta, icon, color
        columns: Number of columns (default 4)
    """
    cols = st.columns(columns)

    for i, metric in enumerate(metrics):
        with cols[i % columns]:
            render_metric_card(
                label=metric.get("label", ""),
                value=metric.get("value", "N/A"),
                delta=metric.get("delta"),
                delta_color=metric.get("delta_color", "normal"),
                icon=metric.get("icon"),
                color=metric.get("color", "primary"),
            )


def render_status_badge(status: str, size: str = "normal") -> str:
    """Generate HTML for a status badge.

    Args:
        status: Status string (running, stopped, error, etc.)
        size: Badge size (small, normal, large)

    Returns:
        HTML string for the badge
    """
    status_lower = status.lower()
    status_class = f"status-{status_lower}"

    # Size classes
    size_styles = {
        "small": "font-size: 0.625rem; padding: 0.25rem 0.5rem;",
        "normal": "font-size: 0.75rem; padding: 0.375rem 0.75rem;",
        "large": "font-size: 0.875rem; padding: 0.5rem 1rem;",
    }
    size_style = size_styles.get(size, size_styles["normal"])

    # Status icons
    status_icons = {
        "running": "●",
        "stopped": "○",
        "error": "✕",
        "starting": "◐",
        "stopping": "◑",
    }
    icon = status_icons.get(status_lower, "●")

    return f"""
    <span class="status-badge {status_class}" style="{size_style}">
        {icon} {status.upper()}
    </span>
    """


def render_progress_bar(
    value: float,
    max_value: float = 100,
    label: Optional[str] = None,
    show_percentage: bool = True,
    color: str = "primary",
) -> None:
    """Render a styled progress bar.

    Args:
        value: Current value
        max_value: Maximum value
        label: Optional label above the bar
        show_percentage: Whether to show percentage text
        color: Bar color (primary, secondary, warning, danger)
    """
    percentage = min(100, (value / max_value * 100)) if max_value > 0 else 0

    accent_colors = {
        "primary": THEME_CONFIG["primary_color"],
        "secondary": THEME_CONFIG["secondary_color"],
        "warning": THEME_CONFIG["warning_color"],
        "danger": THEME_CONFIG["error_color"],
    }
    bar_color = accent_colors.get(color, THEME_CONFIG["primary_color"])

    label_html = f'<div style="margin-bottom: 0.5rem; color: #8B949E; font-size: 0.875rem;">{label}</div>' if label else ""
    percentage_html = f'<span style="color: #FFFFFF; font-weight: 500;">{percentage:.1f}%</span>' if show_percentage else ""

    html = f"""
    {label_html}
    <div style="display: flex; align-items: center; gap: 1rem;">
        <div class="progress-bar" style="flex: 1;">
            <div class="progress-fill" style="width: {percentage}%; background: {bar_color};"></div>
        </div>
        {percentage_html}
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def render_kpi_card(
    title: str,
    value: str,
    subtitle: Optional[str] = None,
    trend: Optional[List[float]] = None,
    trend_label: Optional[str] = None,
) -> None:
    """Render a KPI card with optional sparkline trend.

    Args:
        title: Card title
        value: Main value to display
        subtitle: Optional subtitle/description
        trend: Optional list of values for sparkline
        trend_label: Label for the trend (e.g., "Last 7 days")
    """
    from .charts import create_mini_sparkline

    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown(f"""
        <div style="padding: 0.5rem 0;">
            <div style="color: #8B949E; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em;">{title}</div>
            <div style="font-size: 2rem; font-weight: 700; color: #FFFFFF; margin: 0.25rem 0;">{value}</div>
            {"<div style='color: #6B7280; font-size: 0.75rem;'>" + subtitle + "</div>" if subtitle else ""}
        </div>
        """, unsafe_allow_html=True)

    with col2:
        if trend:
            fig = create_mini_sparkline(trend)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            if trend_label:
                st.caption(trend_label)


def render_stat_grid(stats: List[Dict[str, Any]], columns: int = 3) -> None:
    """Render a grid of statistics.

    Args:
        stats: List of stat dictionaries with keys: label, value, icon
        columns: Number of columns
    """
    cols = st.columns(columns)

    for i, stat in enumerate(stats):
        with cols[i % columns]:
            icon = stat.get("icon", "")
            label = stat.get("label", "")
            value = stat.get("value", "N/A")

            st.markdown(f"""
            <div style="text-align: center; padding: 1rem;">
                <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">{icon}</div>
                <div style="font-size: 1.25rem; font-weight: 600; color: #FFFFFF;">{value}</div>
                <div style="font-size: 0.75rem; color: #8B949E; text-transform: uppercase;">{label}</div>
            </div>
            """, unsafe_allow_html=True)


def render_info_banner(
    message: str,
    type: str = "info",
    icon: Optional[str] = None,
    dismissible: bool = False,
) -> None:
    """Render an information banner.

    Args:
        message: Banner message
        type: Banner type (info, success, warning, error)
        icon: Optional custom icon
        dismissible: Whether the banner can be dismissed
    """
    type_config = {
        "info": {"bg": "rgba(88, 166, 255, 0.1)", "border": "#58A6FF", "icon": "ℹ️"},
        "success": {"bg": "rgba(0, 210, 106, 0.1)", "border": "#00D26A", "icon": "✓"},
        "warning": {"bg": "rgba(240, 180, 41, 0.1)", "border": "#F0B429", "icon": "⚠"},
        "error": {"bg": "rgba(248, 81, 73, 0.1)", "border": "#F85149", "icon": "✕"},
    }

    config = type_config.get(type, type_config["info"])
    display_icon = icon or config["icon"]

    st.markdown(f"""
    <div style="
        background: {config['bg']};
        border-left: 4px solid {config['border']};
        border-radius: 8px;
        padding: 1rem 1.25rem;
        margin: 1rem 0;
        display: flex;
        align-items: center;
        gap: 0.75rem;
    ">
        <span style="font-size: 1.25rem;">{display_icon}</span>
        <span style="color: #FFFFFF; font-size: 0.875rem;">{message}</span>
    </div>
    """, unsafe_allow_html=True)
