"""Tests that emitted events conform to the hand-written JSON Schemas.

Validates both a single-intent run and the 7-intent paper equities flow
against the schemas in docs/schemas/. Uses sim broker only (deterministic).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.runtime.runner import run_stream

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = FIXTURES / "policies" / "base_enforce.yaml"
MARKET = FIXTURES / "states" / "market_simple.json"
SCHEMAS_DIR = Path(__file__).parent.parent / "docs" / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def _make_registry() -> Registry:
    """Build a referencing.Registry for $ref resolution across schema files."""
    resources = []
    for schema_file in SCHEMAS_DIR.glob("*.schema.json"):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        sid = schema.get("$id", schema_file.name)
        resources.append((sid, Resource.from_contents(schema, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


def _make_validator(schema_name: str) -> jsonschema.Validator:
    schema = _load_schema(schema_name)
    registry = _make_registry()
    return jsonschema.Draft202012Validator(schema, registry=registry)


def _load_market() -> MarketSnapshot:
    raw = json.loads(MARKET.read_text(encoding="utf-8"))
    return MarketSnapshot.model_validate(raw)


# ── Single-intent run (demo-style) ───────────────────────────────────


def _run_single_intent(tmp_path):
    """Run a single ALLOW intent and return (audit_events, exec_events, summary_dict)."""
    market = _load_market()
    intents = [
        OrderIntent(
            intent_id="schema-001",
            timestamp="2026-02-24T09:30:01Z",
            strategy_id="demo_strategy",
            account_id="acct_1",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=10,
        ),
    ]
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
    )
    execution = ExecutionState()
    audit_path = tmp_path / "audit.jsonl"
    exec_path = tmp_path / "exec.jsonl"

    summary, fp, fe = run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=audit_path,
        exec_log_path=exec_path,
    )

    audit_events = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    exec_events = [
        json.loads(line)
        for line in exec_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return audit_events, exec_events, summary.to_dict(fp, fe)


# ── Paper equities flow (7 intents) ──────────────────────────────────


def _run_paper_flow(tmp_path):
    """Run the 7-intent paper equities flow and return (audit_events, exec_events, summary_dict)."""
    market = _load_market()
    # Use market_simple.json prices: AAPL=200, TSLA=400
    intents = [
        # 1. ALLOW — small buy
        OrderIntent(
            intent_id="pf-001", timestamp="2026-02-24T09:30:01Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy", order_type="market", qty=10,
        ),
        # 2. ALLOW — small TSLA buy
        OrderIntent(
            intent_id="pf-002", timestamp="2026-02-24T09:30:05Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "TSLA", "asset_class": "equity"},
            side="buy", order_type="market", qty=2,
        ),
        # 3. MODIFY — position cap on AAPL
        OrderIntent(
            intent_id="pf-003", timestamp="2026-02-24T09:31:00Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy", order_type="market", qty=50,
        ),
        # 4. ALLOW — sell some AAPL
        OrderIntent(
            intent_id="pf-004", timestamp="2026-02-24T09:32:00Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="sell", order_type="market", qty=5,
        ),
        # 5. DENY — gross exposure breach (big TSLA buy)
        OrderIntent(
            intent_id="pf-005", timestamp="2026-02-24T09:33:00Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "TSLA", "asset_class": "equity"},
            side="buy", order_type="market", qty=500,
        ),
        # 6. DENY — triggers more violations
        OrderIntent(
            intent_id="pf-006", timestamp="2026-02-24T09:34:00Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "TSLA", "asset_class": "equity"},
            side="buy", order_type="market", qty=500,
        ),
        # 7. DENY — kill switch should be active
        OrderIntent(
            intent_id="pf-007", timestamp="2026-02-24T09:35:00Z",
            strategy_id="momentum_v1", account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy", order_type="market", qty=1,
        ),
    ]
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
    )
    execution = ExecutionState()
    audit_path = tmp_path / "audit.jsonl"
    exec_path = tmp_path / "exec.jsonl"

    summary, fp, fe = run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=audit_path,
        exec_log_path=exec_path,
    )

    audit_events = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    exec_events = [
        json.loads(line)
        for line in exec_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return audit_events, exec_events, summary.to_dict(fp, fe)


# ── Schema validation tests ──────────────────────────────────────────


def test_single_intent_audit_schema(tmp_path):
    """Single-intent audit events match audit_event.schema.json."""
    audit_events, _, _ = _run_single_intent(tmp_path)
    validator = _make_validator("audit_event.schema.json")
    for event in audit_events:
        validator.validate(event)


def test_single_intent_exec_schema(tmp_path):
    """Single-intent exec events match execution_event.schema.json."""
    _, exec_events, _ = _run_single_intent(tmp_path)
    validator = _make_validator("execution_event.schema.json")
    for event in exec_events:
        validator.validate(event)


def test_single_intent_summary_schema(tmp_path):
    """Single-intent run summary matches run_summary.schema.json."""
    _, _, summary_dict = _run_single_intent(tmp_path)
    validator = _make_validator("run_summary.schema.json")
    validator.validate(summary_dict)


def test_paper_flow_audit_schema(tmp_path):
    """Paper flow audit events match audit_event.schema.json."""
    audit_events, _, _ = _run_paper_flow(tmp_path)
    assert len(audit_events) == 7
    validator = _make_validator("audit_event.schema.json")
    for event in audit_events:
        validator.validate(event)


def test_paper_flow_exec_schema(tmp_path):
    """Paper flow exec events match execution_event.schema.json."""
    _, exec_events, _ = _run_paper_flow(tmp_path)
    assert len(exec_events) > 0
    validator = _make_validator("execution_event.schema.json")
    for event in exec_events:
        validator.validate(event)


def test_paper_flow_summary_schema(tmp_path):
    """Paper flow run summary matches run_summary.schema.json."""
    _, _, summary_dict = _run_paper_flow(tmp_path)
    validator = _make_validator("run_summary.schema.json")
    validator.validate(summary_dict)
