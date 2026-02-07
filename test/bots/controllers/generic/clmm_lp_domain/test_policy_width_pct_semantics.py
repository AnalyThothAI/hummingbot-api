import os
import sys
import types
from decimal import Decimal


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
HBOT_ROOT = os.path.join(ROOT, "hummingbot")
for path in (ROOT, HBOT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


# ---- Import target modules (after stubs in conftest.py) ----
sys.modules.pop("bots.controllers.generic.clmm_lp_domain.policies", None)
from bots.controllers.generic.clmm_lp_domain.components import PoolDomainAdapter
from bots.controllers.generic.clmm_lp_domain.policies import MeteoraPolicy, UniswapV3Policy


def _make_cfg(width: Decimal):
    return types.SimpleNamespace(position_width_pct=width, ratio_edge_buffer_pct=Decimal("0"))


def test_meteora_policy_range_plan_for_side_accepts_ratio_and_percent_points():
    domain = PoolDomainAdapter.from_config("AAA-BBB", None)
    policy_pct = MeteoraPolicy(_make_cfg(Decimal("12")), domain)
    policy_ratio = MeteoraPolicy(_make_cfg(Decimal("0.12")), domain)

    plan_pct = policy_pct.range_plan_for_side(Decimal("100"), "base")
    plan_ratio = policy_ratio.range_plan_for_side(Decimal("100"), "base")

    assert plan_pct == plan_ratio


def test_uniswap_policy_range_plan_for_side_accepts_ratio_and_percent_points():
    domain = PoolDomainAdapter.from_config("AAA-BBB", None)
    policy_pct = UniswapV3Policy(_make_cfg(Decimal("12")), domain)
    policy_ratio = UniswapV3Policy(_make_cfg(Decimal("0.12")), domain)
    # Bypass async update and focus on width semantics.
    policy_pct._tick_spacing = 1
    policy_ratio._tick_spacing = 1

    plan_pct = policy_pct.range_plan_for_side(Decimal("100"), "base")
    plan_ratio = policy_ratio.range_plan_for_side(Decimal("100"), "base")

    assert plan_pct == plan_ratio

