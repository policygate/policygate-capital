"""Replay audit events to verify determinism.

Given a recorded audit event, reconstruct the inputs and re-evaluate.
The replayed decision must match the original decision exactly.
"""

from __future__ import annotations

from policygate_capital.engine.decisions import Decision
from policygate_capital.engine.evaluator import evaluate
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.policy import CapitalPolicy
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from typing import Any, Dict


def replay_event(
    event: Dict[str, Any],
    policy: CapitalPolicy,
) -> tuple[Decision, Decision]:
    """Replay a single audit event.

    Returns (original_decision, replayed_decision).
    The caller should assert they are equal.
    """
    intent = OrderIntent.model_validate(event["intent"])
    portfolio = PortfolioState.model_validate(event["portfolio_state"])
    market = MarketSnapshot.model_validate(event["market_snapshot"])
    execution = ExecutionState.model_validate(event["execution_state"])

    original = Decision.model_validate(event["decision"])

    replayed = evaluate(
        intent=intent,
        policy=policy,
        portfolio=portfolio,
        market=market,
        execution=execution,
    )

    return original, replayed


def decisions_match(a: Decision, b: Decision) -> bool:
    """Compare two decisions for logical equality.

    Ignores fields that may vary (none currently, but future-proofs).
    """
    return (
        a.decision == b.decision
        and a.intent_id == b.intent_id
        and a.violations == b.violations
        and a.kill_switch_triggered == b.kill_switch_triggered
        and a.modified_intent == b.modified_intent
    )
