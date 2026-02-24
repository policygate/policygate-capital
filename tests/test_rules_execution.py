"""Tests for execution throttle rules: EXEC-001, EXEC-002."""

from policygate_capital.engine.evaluator import evaluate


def test_global_rate_limit_denies(
    base_policy, intent_spam, portfolio_normal, market_simple, execution_rate_global
):
    """25 orders in last 60s, limit is 20. Deny with EXEC-001."""
    d = evaluate(
        intent_spam, base_policy, portfolio_normal, market_simple, execution_rate_global
    )
    assert d.decision == "DENY"
    assert any(v.rule_id == "EXEC-001" for v in d.violations)


def test_strategy_rate_limit_denies(
    base_policy, intent_spam, portfolio_normal, market_simple, execution_rate_strategy
):
    """15 strategy orders in last 60s, limit is 10. Deny with EXEC-002."""
    d = evaluate(
        intent_spam, base_policy, portfolio_normal, market_simple, execution_rate_strategy
    )
    assert d.decision == "DENY"
    assert any(v.rule_id == "EXEC-002" for v in d.violations)


def test_under_rate_limits_allows(
    base_policy, intent_spam, portfolio_normal, market_simple, execution_normal
):
    """0 orders in last 60s. Should pass throttle checks."""
    d = evaluate(
        intent_spam, base_policy, portfolio_normal, market_simple, execution_normal
    )
    assert d.decision == "ALLOW"


def test_strategy_override_tighter_rate(portfolio_normal, market_simple):
    """Strategy override sets per-strategy limit to 5. 6 orders should deny."""
    from pathlib import Path
    from policygate_capital.util.io import load_policy_yaml
    from policygate_capital.models.intent import OrderIntent
    from policygate_capital.models.state import ExecutionState

    fixtures = Path(__file__).parent / "fixtures"
    policy = load_policy_yaml(fixtures / "policies" / "overrides_strategy.yaml")

    intent = OrderIntent(
        intent_id="strat-rate-001",
        timestamp="2026-02-18T00:00:00Z",
        strategy_id="mean_reversion_v1",
        account_id="acct_1",
        instrument={"symbol": "AAPL", "asset_class": "equity"},
        side="buy",
        order_type="market",
        qty=1,
        limit_price=None,
    )
    execution = ExecutionState(
        orders_last_60s_global=3,
        orders_last_60s_by_strategy={"mean_reversion_v1": 6},
        violations_last_window=[],
        kill_switch_active=False,
    )
    d = evaluate(intent, policy, portfolio_normal, market_simple, execution)
    assert d.decision == "DENY"
    assert any(v.rule_id == "EXEC-002" for v in d.violations)
