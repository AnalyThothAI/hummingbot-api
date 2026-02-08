from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _iter_controller_yaml_paths() -> list[Path]:
    conf_dir = REPO_ROOT / "bots" / "conf" / "controllers"
    instance_dir = REPO_ROOT / "bots" / "instances"
    paths: list[Path] = []
    paths.extend(sorted(conf_dir.glob("*.yml")))
    paths.extend(sorted(instance_dir.glob("**/conf/controllers/*.yml")))
    return paths


def _parse_yaml_scalar(text: str, key: str) -> str | None:
    # Minimal scalar parser for our controller YAML conventions.
    # We intentionally avoid importing yaml libs to keep tests lightweight.
    m = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    if m is None:
        return None
    raw = m.group(1)
    raw = raw.split("#", 1)[0].strip()
    raw = raw.strip("\"'")
    return raw


def _is_clmm_lp_controller_yaml(text: str) -> bool:
    name = _parse_yaml_scalar(text, "controller_name")
    if name is None:
        return False
    return name in {"clmm_lp_uniswap", "clmm_lp_meteora"}


def test_clmm_lp_controller_pct_fields_are_ratios():
    # CLMM LP controller YAML uses ratio semantics for *_pct fields:
    # - 0.05 == 5%
    # Percent-points (e.g. 5 == 5%) are intentionally rejected to avoid ambiguity.
    ratio_keys = {
        "position_width_pct",
        "hysteresis_pct",
        "exit_swap_slippage_pct",
        "stop_loss_pnl_pct",
        "take_profit_pnl_pct",
        "ratio_edge_buffer_pct",  # meteora only
    }

    offenders: list[str] = []
    for path in _iter_controller_yaml_paths():
        text = path.read_text(encoding="utf-8")
        if not _is_clmm_lp_controller_yaml(text):
            continue
        for key in sorted(ratio_keys):
            raw = _parse_yaml_scalar(text, key)
            if raw is None or raw == "" or raw.lower() in {"null", "none"}:
                continue
            try:
                value = Decimal(raw)
            except Exception:
                offenders.append(f"{path}: {key} not a number: {raw!r}")
                continue
            if value < 0:
                offenders.append(f"{path}: {key} must be >= 0 (ratio), got {value}")
                continue
            if value > 1:
                # Provide a migration hint: value in percent-points / 100.
                offenders.append(
                    f"{path}: {key} must be ratio <= 1; got {value}. "
                    f"If you meant {value}%, use {value / Decimal('100')}."
                )

        # Extra safety: slippage is the most dangerous ambiguous parameter.
        raw_slippage = _parse_yaml_scalar(text, "exit_swap_slippage_pct")
        if raw_slippage is not None and raw_slippage != "" and raw_slippage.lower() not in {"null", "none"}:
            try:
                slippage = Decimal(raw_slippage)
            except Exception:
                offenders.append(f"{path}: exit_swap_slippage_pct not a number: {raw_slippage!r}")
            else:
                if slippage > Decimal("0.2"):
                    offenders.append(
                        f"{path}: exit_swap_slippage_pct too high: {slippage} (ratio). "
                        "Safety ceiling is 0.2 (20%)."
                    )

        raw_attempts = _parse_yaml_scalar(text, "max_exit_swap_attempts")
        if raw_attempts is not None and raw_attempts != "" and raw_attempts.lower() not in {"null", "none"}:
            try:
                attempts = int(Decimal(raw_attempts))
            except Exception:
                offenders.append(f"{path}: max_exit_swap_attempts not an int: {raw_attempts!r}")
            else:
                if attempts < 10:
                    offenders.append(
                        f"{path}: max_exit_swap_attempts too low: {attempts}. Recommended minimum is 10."
                    )

    assert offenders == []


def test_pct_to_ratio_rejects_percent_points_to_avoid_ambiguity():
    # This helper will be used by CLMM controllers. It should *not* accept 5 == 5%.
    from bots.controllers.shared.percent import pct_to_ratio

    assert pct_to_ratio("0.05") == Decimal("0.05")
    assert pct_to_ratio(Decimal("1")) == Decimal("1")
    assert pct_to_ratio(None) == Decimal("0")
    assert pct_to_ratio(Decimal("-1")) == Decimal("0")

    with pytest.raises(ValueError):
        pct_to_ratio("5")
