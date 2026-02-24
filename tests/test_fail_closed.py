"""Tests for fail-closed behavior: SYS-001."""

from policygate_capital.engine.evaluator import evaluate
from policygate_capital.models.state import MarketSnapshot


def test_missing_price_denies(
    base_policy, intent_small, portfolio_normal, execution_normal
):
    """Missing price for AAPL → SYS-001 deny."""
    market = MarketSnapshot(
        timestamp="2026-02-18T00:00:00Z",
        prices={},  # No AAPL price
    )
    d = evaluate(intent_small, base_policy, portfolio_normal, market, execution_normal)
    assert d.decision == "DENY"
    assert len(d.violations) == 1
    assert d.violations[0].rule_id == "SYS-001"
    assert "AAPL" in d.violations[0].message


def test_zero_price_denies(
    base_policy, intent_small, portfolio_normal, execution_normal
):
    """Zero price for AAPL → SYS-001 deny."""
    market = MarketSnapshot(
        timestamp="2026-02-18T00:00:00Z",
        prices={"AAPL": 0.0},
    )
    d = evaluate(intent_small, base_policy, portfolio_normal, market, execution_normal)
    assert d.decision == "DENY"
    assert d.violations[0].rule_id == "SYS-001"


def test_negative_price_denies(
    base_policy, intent_small, portfolio_normal, execution_normal
):
    """Negative price → SYS-001 deny."""
    market = MarketSnapshot(
        timestamp="2026-02-18T00:00:00Z",
        prices={"AAPL": -10.0},
    )
    d = evaluate(intent_small, base_policy, portfolio_normal, market, execution_normal)
    assert d.decision == "DENY"
    assert d.violations[0].rule_id == "SYS-001"
