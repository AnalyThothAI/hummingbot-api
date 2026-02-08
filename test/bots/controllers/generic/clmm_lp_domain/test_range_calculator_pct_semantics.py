import os
import sys
from decimal import Decimal

import pytest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


from bots.controllers.generic.clmm_lp_domain.range_calculator import RangeCalculator


def test_geometric_bounds_accepts_ratio_width():
    center = Decimal("100")

    bounds_ratio = RangeCalculator.geometric_bounds(center, Decimal("0.12"))  # also 12%

    assert bounds_ratio is not None


def test_geometric_bounds_rejects_percent_points_width():
    center = Decimal("100")

    with pytest.raises(ValueError):
        RangeCalculator.geometric_bounds(center, Decimal("12"))  # 12 (invalid)
