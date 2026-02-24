"""Tests for loss rules: LOSS-001, LOSS-002."""

from policygate_capital.engine.evaluator import evaluate


def test_daily_loss_denies(
    base_policy, intent_small, portfolio_daily_loss, market_simple, execution_normal
):
    """Equity $97k, SOD $100k → daily return -3%. Limit is -2%. Deny."""
    d = evaluate(
        intent_small, base_policy, portfolio_daily_loss, market_simple, execution_normal
    )
    assert d.decision == "DENY"
    assert any(v.rule_id == "LOSS-001" for v in d.violations)


def test_drawdown_denies_and_trips_kill_switch(
    base_policy, intent_small, portfolio_drawdown, market_simple, execution_normal
):
    """Equity $90k, peak $100k → drawdown 10%. Limit is 5%. Deny + kill switch."""
    d = evaluate(
        intent_small, base_policy, portfolio_drawdown, market_simple, execution_normal
    )
    assert d.decision == "DENY"
    assert any(v.rule_id == "LOSS-002" for v in d.violations)
    assert d.kill_switch_triggered is True


def test_drawdown_at_exactly_limit_denies(
    base_policy, intent_small, market_simple, execution_normal
):
    """Drawdown exactly at 5% boundary should deny."""
    from policygate_capital.models.state import PortfolioState

    portfolio = PortfolioState(
        equity=95000.0,
        start_of_day_equity=100000.0,
        peak_equity=100000.0,
        positions={},
    )
    d = evaluate(
        intent_small, base_policy, portfolio, market_simple, execution_normal
    )
    assert d.decision == "DENY"
    rule_ids = {v.rule_id for v in d.violations}
    assert "LOSS-002" in rule_ids


def test_no_loss_allows(
    base_policy, intent_small, portfolio_normal, market_simple, execution_normal
):
    """Normal portfolio (no loss) should pass loss checks."""
    d = evaluate(
        intent_small, base_policy, portfolio_normal, market_simple, execution_normal
    )
    assert d.decision == "ALLOW"
