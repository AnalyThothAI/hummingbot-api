"""Chart components for LP Dashboard using Plotly."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import numpy as np

from .theme import get_default_layout, get_color_scheme

colors = get_color_scheme()


def create_pnl_chart(
    timestamps: List[datetime],
    pnl_values: List[float],
    title: str = "PnL Over Time",
    height: int = 350,
) -> go.Figure:
    """Create a PnL line chart with gradient fill."""
    layout = get_default_layout(title=title, height=height)

    # Determine if overall PnL is positive or negative
    final_pnl = pnl_values[-1] if pnl_values else 0
    line_color = colors["pnl_positive"] if final_pnl >= 0 else colors["pnl_negative"]
    fill_color = f"rgba({34 if final_pnl >= 0 else 248}, {197 if final_pnl >= 0 else 81}, {94 if final_pnl >= 0 else 73}, 0.2)"

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=timestamps,
        y=pnl_values,
        mode="lines",
        name="PnL",
        line=dict(color=line_color, width=2),
        fill="tozeroy",
        fillcolor=fill_color,
        hovertemplate="<b>PnL:</b> $%{y:,.2f}<br><b>Time:</b> %{x}<extra></extra>",
    ))

    # Add zero line
    fig.add_hline(y=0, line_dash="dash", line_color=colors["neutral"], line_width=1)

    fig.update_layout(**layout)
    return fig


def create_position_chart(
    current_price: float,
    lower_price: float,
    upper_price: float,
    entry_price: Optional[float] = None,
    title: str = "Position Range",
    height: int = 300,
) -> go.Figure:
    """Create a position range visualization chart."""
    layout = get_default_layout(title=title, height=height, show_legend=False)

    # Calculate price range for display
    price_range = upper_price - lower_price
    margin = price_range * 0.2
    y_min = lower_price - margin
    y_max = upper_price + margin

    fig = go.Figure()

    # Add range fill
    fig.add_trace(go.Scatter(
        x=[0, 1, 1, 0, 0],
        y=[lower_price, lower_price, upper_price, upper_price, lower_price],
        fill="toself",
        fillcolor=colors["range_fill"],
        line=dict(color=colors["in_range"], width=2),
        name="Price Range",
        hoverinfo="skip",
    ))

    # Add current price line
    in_range = lower_price <= current_price <= upper_price
    price_color = colors["in_range"] if in_range else colors["out_of_range"]

    fig.add_trace(go.Scatter(
        x=[0, 1],
        y=[current_price, current_price],
        mode="lines",
        name="Current Price",
        line=dict(color=price_color, width=3),
        hovertemplate=f"<b>Current Price:</b> ${current_price:,.4f}<extra></extra>",
    ))

    # Add entry price if provided
    if entry_price:
        fig.add_trace(go.Scatter(
            x=[0, 1],
            y=[entry_price, entry_price],
            mode="lines",
            name="Entry Price",
            line=dict(color=colors["warning_color"], width=2, dash="dot"),
            hovertemplate=f"<b>Entry Price:</b> ${entry_price:,.4f}<extra></extra>",
        ))

    # Add annotations
    fig.add_annotation(
        x=1.05, y=upper_price,
        text=f"Upper: ${upper_price:,.4f}",
        showarrow=False,
        font=dict(color=colors["upper_bound"], size=11),
        xanchor="left",
    )

    fig.add_annotation(
        x=1.05, y=lower_price,
        text=f"Lower: ${lower_price:,.4f}",
        showarrow=False,
        font=dict(color=colors["lower_bound"], size=11),
        xanchor="left",
    )

    fig.add_annotation(
        x=1.05, y=current_price,
        text=f"Current: ${current_price:,.4f}",
        showarrow=False,
        font=dict(color=price_color, size=11, weight="bold"),
        xanchor="left",
    )

    fig.update_layout(**layout)
    fig.update_xaxes(visible=False, range=[-0.1, 1.3])
    fig.update_yaxes(range=[y_min, y_max])

    return fig


def create_performance_chart(
    data: Dict[str, List],
    title: str = "Performance Overview",
    height: int = 400,
) -> go.Figure:
    """Create a multi-metric performance chart."""
    layout = get_default_layout(title=title, height=height)

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        row_heights=[0.7, 0.3],
        subplot_titles=["Portfolio Value", "Volume"],
    )

    timestamps = data.get("timestamps", [])
    portfolio_values = data.get("portfolio_values", [])
    volumes = data.get("volumes", [])

    # Portfolio value line
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=portfolio_values,
        mode="lines",
        name="Portfolio Value",
        line=dict(color=colors["metric_primary"], width=2),
        fill="tozeroy",
        fillcolor="rgba(0, 210, 106, 0.1)",
    ), row=1, col=1)

    # Volume bars
    if volumes:
        fig.add_trace(go.Bar(
            x=timestamps,
            y=volumes,
            name="Volume",
            marker=dict(color=colors["volume"], opacity=0.7),
        ), row=2, col=1)

    fig.update_layout(**layout)
    return fig


def create_price_range_chart(
    timestamps: List[datetime],
    prices: List[float],
    lower_prices: List[float],
    upper_prices: List[float],
    title: str = "Price vs Range",
    height: int = 400,
) -> go.Figure:
    """Create a chart showing price movement relative to LP range."""
    layout = get_default_layout(title=title, height=height)

    fig = go.Figure()

    # Upper bound
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=upper_prices,
        mode="lines",
        name="Upper Bound",
        line=dict(color=colors["upper_bound"], width=1, dash="dash"),
        hovertemplate="<b>Upper:</b> $%{y:,.4f}<extra></extra>",
    ))

    # Lower bound
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=lower_prices,
        mode="lines",
        name="Lower Bound",
        line=dict(color=colors["lower_bound"], width=1, dash="dash"),
        fill="tonexty",
        fillcolor="rgba(0, 210, 106, 0.1)",
        hovertemplate="<b>Lower:</b> $%{y:,.4f}<extra></extra>",
    ))

    # Price line
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=prices,
        mode="lines",
        name="Price",
        line=dict(color=colors["price_line"], width=2),
        hovertemplate="<b>Price:</b> $%{y:,.4f}<extra></extra>",
    ))

    fig.update_layout(**layout)
    return fig


def create_fees_chart(
    timestamps: List[datetime],
    fees: List[float],
    cumulative: bool = True,
    title: str = "Fees Collected",
    height: int = 300,
) -> go.Figure:
    """Create a fees collection chart."""
    layout = get_default_layout(title=title, height=height)

    fig = go.Figure()

    if cumulative:
        # Cumulative line
        cumulative_fees = np.cumsum(fees).tolist()
        fig.add_trace(go.Scatter(
            x=timestamps,
            y=cumulative_fees,
            mode="lines",
            name="Cumulative Fees",
            line=dict(color=colors["fees"], width=2),
            fill="tozeroy",
            fillcolor="rgba(240, 180, 41, 0.2)",
            hovertemplate="<b>Total Fees:</b> $%{y:,.4f}<extra></extra>",
        ))
    else:
        # Individual fee bars
        fig.add_trace(go.Bar(
            x=timestamps,
            y=fees,
            name="Fees",
            marker=dict(color=colors["fees"], opacity=0.8),
            hovertemplate="<b>Fee:</b> $%{y:,.4f}<extra></extra>",
        ))

    fig.update_layout(**layout)
    return fig


def create_allocation_pie(
    labels: List[str],
    values: List[float],
    title: str = "Portfolio Allocation",
    height: int = 350,
) -> go.Figure:
    """Create a portfolio allocation pie chart."""
    layout = get_default_layout(title=title, height=height, show_legend=True)

    # Custom color sequence
    color_sequence = [
        colors["metric_primary"],
        colors["metric_secondary"],
        colors["warning_color"],
        colors["upper_bound"],
        "#EC4899",  # Pink
        "#14B8A6",  # Teal
    ]

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        marker=dict(colors=color_sequence[:len(labels)]),
        textinfo="label+percent",
        textposition="outside",
        hovertemplate="<b>%{label}</b><br>Value: $%{value:,.2f}<br>%{percent}<extra></extra>",
    ))

    fig.update_layout(**layout)
    return fig


def create_strategy_comparison_chart(
    strategies: List[Dict[str, Any]],
    metric: str = "pnl",
    title: str = "Strategy Comparison",
    height: int = 350,
) -> go.Figure:
    """Create a bar chart comparing strategies."""
    layout = get_default_layout(title=title, height=height)

    names = [s.get("name", "Unknown") for s in strategies]
    values = [s.get(metric, 0) for s in strategies]

    # Color based on positive/negative
    bar_colors = [
        colors["pnl_positive"] if v >= 0 else colors["pnl_negative"]
        for v in values
    ]

    fig = go.Figure(go.Bar(
        x=names,
        y=values,
        marker=dict(color=bar_colors, opacity=0.8),
        hovertemplate="<b>%{x}</b><br>%{y:,.2f}<extra></extra>",
    ))

    fig.add_hline(y=0, line_dash="dash", line_color=colors["neutral"], line_width=1)

    fig.update_layout(**layout)
    return fig


def create_mini_sparkline(
    values: List[float],
    width: int = 150,
    height: int = 50,
) -> go.Figure:
    """Create a minimal sparkline chart."""
    fig = go.Figure()

    final_value = values[-1] if values else 0
    line_color = colors["pnl_positive"] if final_value >= 0 else colors["pnl_negative"]

    fig.add_trace(go.Scatter(
        y=values,
        mode="lines",
        line=dict(color=line_color, width=1.5),
        fill="tozeroy",
        fillcolor=f"rgba({34 if final_value >= 0 else 248}, {197 if final_value >= 0 else 81}, {94 if final_value >= 0 else 73}, 0.2)",
        hoverinfo="skip",
    ))

    fig.update_layout(
        width=width,
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )

    return fig
