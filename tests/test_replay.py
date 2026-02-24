"""Tests for audit emission and replay determinism."""

import json
from pathlib import Path

from policygate_capital.engine.audit import (
    build_audit_event,
    read_audit_events,
    write_audit_event,
)
from policygate_capital.engine.evaluator import evaluate
from policygate_capital.engine.replay import decisions_match, replay_event
from policygate_capital.util.hashing import policy_hash


def test_audit_event_roundtrip(
    tmp_path,
    base_policy,
    intent_small,
    portfolio_normal,
    market_simple,
    execution_normal,
):
    """Write an audit event to JSONL, read it back, verify structure."""
    decision = evaluate(
        intent_small, base_policy, portfolio_normal, market_simple, execution_normal
    )
    p_hash = policy_hash(
        (Path(__file__).parent / "fixtures" / "policies" / "base_enforce.yaml")
        .read_text(encoding="utf-8")
    )
    event = build_audit_event(
        decision=decision,
        intent=intent_small,
        portfolio=portfolio_normal,
        market=market_simple,
        execution=execution_normal,
        policy_hash=p_hash,
        engine_version="0.1.0-test",
    )

    audit_file = tmp_path / "audit.jsonl"
    write_audit_event(audit_file, event)

    events = read_audit_events(audit_file)
    assert len(events) == 1
    assert events[0]["policy_hash"] == p_hash
    assert events[0]["decision"]["decision"] == "ALLOW"
    assert events[0]["intent"]["intent_id"] == intent_small.intent_id


def test_replay_reproduces_allow(
    tmp_path,
    base_policy,
    intent_small,
    portfolio_normal,
    market_simple,
    execution_normal,
):
    """Replay an ALLOW decision and verify determinism."""
    decision = evaluate(
        intent_small, base_policy, portfolio_normal, market_simple, execution_normal
    )
    p_hash = policy_hash(
        (Path(__file__).parent / "fixtures" / "policies" / "base_enforce.yaml")
        .read_text(encoding="utf-8")
    )
    event = build_audit_event(
        decision=decision,
        intent=intent_small,
        portfolio=portfolio_normal,
        market=market_simple,
        execution=execution_normal,
        policy_hash=p_hash,
    )

    original, replayed = replay_event(event, base_policy)
    assert decisions_match(original, replayed)


def test_replay_reproduces_deny(
    tmp_path,
    base_policy,
    intent_small,
    portfolio_daily_loss,
    market_simple,
    execution_normal,
):
    """Replay a DENY decision (daily loss) and verify determinism."""
    decision = evaluate(
        intent_small, base_policy, portfolio_daily_loss, market_simple, execution_normal
    )
    p_hash = policy_hash(
        (Path(__file__).parent / "fixtures" / "policies" / "base_enforce.yaml")
        .read_text(encoding="utf-8")
    )
    event = build_audit_event(
        decision=decision,
        intent=intent_small,
        portfolio=portfolio_daily_loss,
        market=market_simple,
        execution=execution_normal,
        policy_hash=p_hash,
    )

    original, replayed = replay_event(event, base_policy)
    assert decisions_match(original, replayed)
    assert original.decision == "DENY"


def test_multiple_events_appended(
    tmp_path,
    base_policy,
    intent_small,
    portfolio_normal,
    portfolio_daily_loss,
    market_simple,
    execution_normal,
):
    """Multiple events are appended to the same JSONL file."""
    audit_file = tmp_path / "audit.jsonl"
    p_hash = "test-hash"

    for portfolio in [portfolio_normal, portfolio_daily_loss]:
        decision = evaluate(
            intent_small, base_policy, portfolio, market_simple, execution_normal
        )
        event = build_audit_event(
            decision=decision,
            intent=intent_small,
            portfolio=portfolio,
            market=market_simple,
            execution=execution_normal,
            policy_hash=p_hash,
        )
        write_audit_event(audit_file, event)

    events = read_audit_events(audit_file)
    assert len(events) == 2
    assert events[0]["decision"]["decision"] == "ALLOW"
    assert events[1]["decision"]["decision"] == "DENY"
