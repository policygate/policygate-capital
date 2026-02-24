"""Tests for kill switch: KILL-001."""

from policygate_capital.engine.evaluator import evaluate
from policygate_capital.models.state import ExecutionState


def test_kill_switch_active_denies(
    base_policy, intent_small, portfolio_normal, market_simple
):
    """When kill switch is active, all orders are denied immediately."""
    execution = ExecutionState(
        orders_last_60s_global=0,
        orders_last_60s_by_strategy={},
        violations_last_window=[],
        kill_switch_active=True,
    )
    d = evaluate(intent_small, base_policy, portfolio_normal, market_simple, execution)
    assert d.decision == "DENY"
    assert len(d.violations) == 1
    assert d.violations[0].rule_id == "KILL-001"
    assert d.violations[0].severity == "CRIT"


def test_kill_switch_inactive_allows(
    base_policy, intent_small, portfolio_normal, market_simple, execution_normal
):
    """Kill switch inactive, normal conditions → ALLOW."""
    d = evaluate(
        intent_small, base_policy, portfolio_normal, market_simple, execution_normal
    )
    assert d.decision == "ALLOW"


def test_kill_switch_checked_before_other_rules(
    base_policy, intent_small, portfolio_daily_loss, market_simple
):
    """Kill switch should be checked first — only KILL-001 in violations."""
    execution = ExecutionState(
        orders_last_60s_global=100,
        orders_last_60s_by_strategy={"demo_strategy": 100},
        violations_last_window=[],
        kill_switch_active=True,
    )
    d = evaluate(
        intent_small, base_policy, portfolio_daily_loss, market_simple, execution
    )
    assert d.decision == "DENY"
    # Only kill switch violation — other rules not evaluated
    assert len(d.violations) == 1
    assert d.violations[0].rule_id == "KILL-001"
