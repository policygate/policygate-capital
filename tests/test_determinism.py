"""Determinism: same inputs must yield identical outputs across repeated calls."""

from policygate_capital.engine.evaluator import evaluate


def test_deterministic_allow(
    base_policy, intent_small, portfolio_normal, market_simple, execution_normal
):
    results = []
    for _ in range(5):
        d = evaluate(intent_small, base_policy, portfolio_normal, market_simple, execution_normal)
        results.append(d.model_dump(mode="json"))

    first = results[0]
    for r in results[1:]:
        assert r == first, "Non-deterministic output detected"


def test_deterministic_deny(
    base_policy, intent_small, portfolio_daily_loss, market_simple, execution_normal
):
    results = []
    for _ in range(5):
        d = evaluate(intent_small, base_policy, portfolio_daily_loss, market_simple, execution_normal)
        results.append(d.model_dump(mode="json"))

    first = results[0]
    for r in results[1:]:
        assert r == first, "Non-deterministic output detected"
