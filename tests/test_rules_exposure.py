"""Tests for exposure rules: EXP-001, EXP-002, EXP-003."""

from policygate_capital.engine.evaluator import evaluate
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import PortfolioState


def test_small_trade_allowed(
    base_policy, intent_small, portfolio_normal, market_simple, execution_normal
):
    """A 10-share AAPL buy at $200 = $2k on $100k equity = 2%. Limit is 10%."""
    d = evaluate(intent_small, base_policy, portfolio_normal, market_simple, execution_normal)
    assert d.decision == "ALLOW"
    assert d.violations == []


def test_position_breach_triggers_modify(
    base_policy, intent_breach_position, portfolio_normal, market_simple, execution_normal
):
    """200 shares * $200 = $40k on $100k equity = 40%. Limit is 10%. Should MODIFY."""
    d = evaluate(
        intent_breach_position, base_policy, portfolio_normal, market_simple, execution_normal
    )
    assert d.decision == "MODIFY"
    assert any(v.rule_id == "EXP-001" for v in d.violations)
    assert d.modified_intent is not None
    assert d.modified_intent.qty < intent_breach_position.qty
    # Modified qty should produce position <= 10% of equity
    max_value = 0.10 * 100000.0
    assert d.modified_intent.qty * 200.0 <= max_value + 0.01


def test_gross_exposure_breach_denies(
    base_policy, intent_breach_gross, portfolio_normal, market_simple, execution_normal
):
    """5000 shares * $200 = $1M on $100k equity = 10x. Limit is 2x. Deny."""
    d = evaluate(
        intent_breach_gross, base_policy, portfolio_normal, market_simple, execution_normal
    )
    assert d.decision == "DENY"
    rule_ids = {v.rule_id for v in d.violations}
    assert "EXP-001" in rule_ids or "EXP-002" in rule_ids


def test_net_exposure_breach_denies(base_policy, market_simple, execution_normal):
    """Large long position pushes net exposure over 1.0x limit."""
    portfolio = PortfolioState(
        equity=100000.0,
        start_of_day_equity=100000.0,
        peak_equity=100000.0,
        positions={"MSFT": 400},  # Assume MSFT not priced → ignored
    )
    # Buy enough AAPL to breach net exposure
    intent = OrderIntent(
        intent_id="net-test-001",
        timestamp="2026-02-18T00:00:00Z",
        strategy_id="demo_strategy",
        account_id="acct_1",
        instrument={"symbol": "AAPL", "asset_class": "equity"},
        side="buy",
        order_type="market",
        qty=600,
        limit_price=None,
    )
    d = evaluate(intent, base_policy, portfolio, market_simple, execution_normal)
    # 600 * $200 = $120k, net = $120k / $100k = 1.2x > 1.0x limit
    assert d.decision in ("DENY", "MODIFY")


def test_symbol_override_tighter_limit(market_simple, execution_normal):
    """Symbol override for AAPL sets max_position_pct to 5%."""
    from policygate_capital.util.io import load_policy_yaml
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures"
    policy = load_policy_yaml(fixtures / "policies" / "overrides_symbol.yaml")

    portfolio = PortfolioState(
        equity=100000.0,
        start_of_day_equity=100000.0,
        peak_equity=100000.0,
        positions={},
    )
    # 30 shares * $200 = $6k = 6% > 5% symbol limit, but < 10% default
    intent = OrderIntent(
        intent_id="override-test-001",
        timestamp="2026-02-18T00:00:00Z",
        strategy_id="demo_strategy",
        account_id="acct_1",
        instrument={"symbol": "AAPL", "asset_class": "equity"},
        side="buy",
        order_type="market",
        qty=30,
        limit_price=None,
    )
    d = evaluate(intent, policy, portfolio, market_simple, execution_normal)
    assert d.decision == "MODIFY"
    assert any(v.rule_id == "EXP-001" for v in d.violations)
