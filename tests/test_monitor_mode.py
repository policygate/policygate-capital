"""Tests for monitor mode: violations logged but decision is always ALLOW."""

from pathlib import Path

from policygate_capital.engine.evaluator import evaluate
from policygate_capital.models.state import ExecutionState, MarketSnapshot
from policygate_capital.util.io import load_policy_yaml

FIXTURES = Path(__file__).parent / "fixtures"


def _monitor_policy():
    return load_policy_yaml(FIXTURES / "policies" / "base_monitor.yaml")


def test_monitor_allows_despite_daily_loss(
    intent_small, portfolio_daily_loss, market_simple, execution_normal
):
    """Monitor mode: daily loss violation logged but decision is ALLOW."""
    policy = _monitor_policy()
    d = evaluate(intent_small, policy, portfolio_daily_loss, market_simple, execution_normal)
    assert d.decision == "ALLOW"
    assert any(v.rule_id == "LOSS-001" for v in d.violations)


def test_monitor_allows_despite_kill_switch(
    intent_small, portfolio_normal, market_simple
):
    """Monitor mode: kill switch violation logged but decision is ALLOW."""
    policy = _monitor_policy()
    execution = ExecutionState(
        orders_last_60s_global=0,
        orders_last_60s_by_strategy={},
        violations_last_window=[],
        kill_switch_active=True,
    )
    d = evaluate(intent_small, policy, portfolio_normal, market_simple, execution)
    assert d.decision == "ALLOW"
    assert any(v.rule_id == "KILL-001" for v in d.violations)


def test_monitor_allows_despite_rate_limit(
    intent_spam, portfolio_normal, market_simple, execution_rate_global
):
    """Monitor mode: rate limit violation logged but decision is ALLOW."""
    policy = _monitor_policy()
    d = evaluate(intent_spam, policy, portfolio_normal, market_simple, execution_rate_global)
    assert d.decision == "ALLOW"
    assert any(v.rule_id == "EXEC-001" for v in d.violations)


def test_monitor_allows_despite_exposure_breach(
    intent_breach_position, portfolio_normal, market_simple, execution_normal
):
    """Monitor mode: exposure violation logged but decision is ALLOW."""
    policy = _monitor_policy()
    d = evaluate(
        intent_breach_position, policy, portfolio_normal, market_simple, execution_normal
    )
    assert d.decision == "ALLOW"
    assert any(v.rule_id == "EXP-001" for v in d.violations)


def test_monitor_still_denies_missing_price(
    intent_small, portfolio_normal, execution_normal
):
    """Monitor mode: SYS-001 (missing price) still denies — can't evaluate without data."""
    policy = _monitor_policy()
    market = MarketSnapshot(timestamp="2026-02-18T00:00:00Z", prices={})
    d = evaluate(intent_small, policy, portfolio_normal, market, execution_normal)
    assert d.decision == "DENY"
    assert d.violations[0].rule_id == "SYS-001"


def test_monitor_no_violations_still_allows(
    intent_small, portfolio_normal, market_simple, execution_normal
):
    """Monitor mode with no violations: clean ALLOW."""
    policy = _monitor_policy()
    d = evaluate(intent_small, policy, portfolio_normal, market_simple, execution_normal)
    assert d.decision == "ALLOW"
    assert d.violations == []
