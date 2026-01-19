"""Theme configuration for LP Dashboard visualizations."""
from typing import Dict, Any, Optional


def get_default_layout(title: Optional[str] = None, height: int = 400, width: int = 1800) -> Dict[str, Any]:
    """Get default Plotly layout configuration."""
    layout = {
        "template": "plotly_dark",
        "plot_bgcolor": "rgba(0, 0, 0, 0)",
        "paper_bgcolor": "rgba(0, 0, 0, 0.1)",
        "font": {"color": "white", "size": 12},
        "height": height,
        "width": width,
        "margin": {"l": 20, "r": 20, "t": 50, "b": 20},
        "xaxis_rangeslider_visible": False,
        "hovermode": "x unified",
        "showlegend": False,
    }
    if title:
        layout["title"] = title
    return layout


def get_color_scheme() -> Dict[str, str]:
    """Get color scheme for visualizations."""
    return {
        # Status colors
        "running": "#00D26A",
        "stopped": "#6B7280",
        "error": "#F85149",

        # Trading colors
        "profit": "#00D26A",
        "loss": "#F85149",
        "neutral": "#6B7280",
        "buy": "#00D26A",
        "sell": "#F85149",

        # LP specific
        "in_range": "#00D26A",
        "out_of_range": "#F85149",
        "lower_bound": "#F0B429",
        "upper_bound": "#7C3AED",
        "current_price": "#58A6FF",
        "fees": "#F0B429",

        # Chart colors
        "price_line": "#58A6FF",
        "volume": "#F0B429",
        "pnl_positive": "#00D26A",
        "pnl_negative": "#F85149",
    }
