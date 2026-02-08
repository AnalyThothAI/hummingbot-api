from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional


def pct_to_ratio(pct: Optional[Any]) -> Decimal:
    """Convert a controller YAML *_pct value into a ratio (0-1).

    Policy:
    - Accept ratio inputs in [0, 1]. Example: 0.05 == 5%.
    - Reject percent-points inputs (> 1) to avoid ambiguity (e.g. 5 could mean 5%).

    Note: Gateway endpoints often use percent-points (0-100). Controller configs do not.
    """
    if pct is None:
        return Decimal("0")

    try:
        value = Decimal(str(pct))
    except Exception:
        return Decimal("0")

    if value <= 0:
        return Decimal("0")

    if value > 1:
        raise ValueError(
            f"Expected ratio in [0, 1] for *_pct fields (e.g. 0.05 for 5%), got {pct!r}."
        )

    return value

