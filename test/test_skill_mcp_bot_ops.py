from pathlib import Path


def test_mcp_bot_ops_skill_has_required_sections_and_guardrails():
    """
    This repo relies on `skills/mcp-bot-ops` for safe on-chain operations.
    Keep a few hard requirements from regressing (risk thresholds, confirmations,
    and config calibration steps).
    """
    repo_root = Path(__file__).resolve().parents[1]
    skill_path = repo_root / "skills" / "mcp-bot-ops" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    # Basic structure
    assert "# MCP Bot Ops" in text
    assert "Non-goals" in text
    assert "Safety" in text and "risk" in text

    # Swap guardrails
    for required in (
        "gateway_swap_quote",
        "gateway_swap_execute",
        "gateway_allowances",
        "gateway_approve",
        "priceImpactPct",
        "slippagePct",
        "priceImpactPct >= 3",
        "slippagePct > 1",
        "HIGH_PRICE_IMPACT",
        "HIGH_SLIPPAGE",
        "UNLIMITED_APPROVE",
        "quote freshness",
        "spender",
        "pancakeswap/router",
        "uniswap/router",
    ):
        assert required in text

    # Deploy/config calibration requirements
    for required in (
        "deploy_v2_workflow_plan",
        "gateway_restart",
        "gateway_status",
        "controller_config_template",
        "controller_config_validate",
        "controller_config_upsert",
        "id == YAML basename",
    ):
        assert required in text

