"""Runner chaos tests: broker failures mid-run.

Tests operational failure modes, not governance correctness.
Verifies the v0.1 failure contract:
  - Audit event always written (pre-submit)
  - ORDER_REJECTED exec event emitted on broker exception
  - Exception propagates (fail-loud)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from policygate_capital.adapters.broker import Fill
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.runtime.runner import run_stream

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = FIXTURES / "policies" / "base_enforce.yaml"


def _make_market() -> MarketSnapshot:
    raw = json.loads(
        (FIXTURES / "states" / "market_simple.json").read_text(encoding="utf-8")
    )
    return MarketSnapshot.model_validate(raw)


def _make_intent(intent_id: str = "chaos-001", qty: float = 5.0) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        timestamp="2026-02-24T09:30:01Z",
        strategy_id="demo_strategy",
        account_id="acct_1",
        instrument={"symbol": "AAPL", "asset_class": "equity"},
        side="buy",
        order_type="market",
        qty=qty,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── Chaos Brokers ────────────────────────────────────────────────────


class ThrottlingBroker:
    """Broker that raises on first N submit() calls then succeeds."""

    def __init__(self, fail_count: int = 1) -> None:
        self._fail_count = fail_count
        self._call_count = 0
        self._fills: list[Fill] = []
        self._next_id = 1

    def submit(self, intent: OrderIntent, market: MarketSnapshot) -> str:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise ConnectionError("429 Too Many Requests")

        order_id = f"THROTTLE-{self._next_id:04d}"
        self._next_id += 1
        price = market.prices.get(intent.instrument.symbol, 0.0)
        self._fills.append(
            Fill(
                order_id=order_id,
                symbol=intent.instrument.symbol,
                side=intent.side,
                qty=intent.qty,
                price=price,
                timestamp=intent.timestamp,
            )
        )
        return order_id

    def cancel(self, order_id: str) -> None:
        pass

    def poll_fills(self, since_ts: str | None = None) -> List[Fill]:
        fills = list(self._fills)
        self._fills.clear()
        return fills


class TimeoutBroker:
    """Broker where submit() always times out."""

    def submit(self, intent: OrderIntent, market: MarketSnapshot) -> str:
        raise TimeoutError("broker connection timed out")

    def cancel(self, order_id: str) -> None:
        pass

    def poll_fills(self, since_ts: str | None = None) -> List[Fill]:
        return []


# ── Tests ────────────────────────────────────────────────────────────


def test_broker_throttle_mid_run(tmp_path):
    """When broker.submit() raises (429 throttle):
    - Audit event was written (audit precedes submit)
    - ORDER_REJECTED exec event was emitted
    - Exception propagates (fail-loud)
    """
    market = _make_market()
    intents = [_make_intent("throttle-001", qty=5)]
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
    )
    execution = ExecutionState()
    audit_path = tmp_path / "audit.jsonl"
    exec_path = tmp_path / "exec.jsonl"

    broker = ThrottlingBroker(fail_count=1)

    with pytest.raises(ConnectionError, match="429"):
        run_stream(
            policy_path=POLICY,
            intents=intents,
            portfolio=portfolio,
            execution=execution,
            market=market,
            audit_log_path=audit_path,
            broker=broker,
            exec_log_path=exec_path,
        )

    # Audit was written before submit
    audit_events = _read_jsonl(audit_path)
    assert len(audit_events) == 1
    assert audit_events[0]["intent"]["intent_id"] == "throttle-001"
    assert audit_events[0]["decision"]["decision"] == "ALLOW"

    # ORDER_REJECTED exec event was emitted before re-raise
    exec_events = _read_jsonl(exec_path)
    assert len(exec_events) == 1
    assert exec_events[0]["event"] == "ORDER_REJECTED"
    assert exec_events[0]["intent_id"] == "throttle-001"
    assert "run_id" in exec_events[0]


def test_broker_timeout_emits_rejected(tmp_path):
    """When broker.submit() times out:
    - Audit exists
    - ORDER_REJECTED exec event emitted (no SUBMITTED or FILLED)
    - Exception propagates
    """
    market = _make_market()
    intents = [_make_intent("timeout-001", qty=5)]
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
    )
    execution = ExecutionState()
    audit_path = tmp_path / "audit.jsonl"
    exec_path = tmp_path / "exec.jsonl"

    broker = TimeoutBroker()

    with pytest.raises(TimeoutError, match="timed out"):
        run_stream(
            policy_path=POLICY,
            intents=intents,
            portfolio=portfolio,
            execution=execution,
            market=market,
            audit_log_path=audit_path,
            broker=broker,
            exec_log_path=exec_path,
        )

    # Audit written
    audit_events = _read_jsonl(audit_path)
    assert len(audit_events) == 1
    assert audit_events[0]["decision"]["decision"] == "ALLOW"

    # Only ORDER_REJECTED, no SUBMITTED or FILLED
    exec_events = _read_jsonl(exec_path)
    assert len(exec_events) == 1
    assert exec_events[0]["event"] == "ORDER_REJECTED"
    event_types = {e["event"] for e in exec_events}
    assert "ORDER_SUBMITTED" not in event_types
    assert "ORDER_FILLED" not in event_types


def test_broker_throttle_propagates_not_swallowed(tmp_path):
    """Runner does NOT silently swallow broker errors.

    This is the correct fail-loud behavior for v0.1.
    Regression baseline for when retry logic is added later.
    """
    market = _make_market()
    intents = [
        _make_intent("recover-001", qty=5),
        _make_intent("recover-002", qty=5),
    ]
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
    )
    execution = ExecutionState()

    broker = ThrottlingBroker(fail_count=1)

    # First intent raises, second never evaluated
    with pytest.raises(ConnectionError):
        run_stream(
            policy_path=POLICY,
            intents=intents,
            portfolio=portfolio,
            execution=execution,
            market=market,
            broker=broker,
        )
