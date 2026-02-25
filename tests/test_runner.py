"""Tests for the runtime runner: stream evaluation, state evolution, kill switch."""

from __future__ import annotations

import json
from pathlib import Path

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


def _load_market() -> MarketSnapshot:
    raw = json.loads(MARKET.read_text(encoding="utf-8"))
    return MarketSnapshot.model_validate(raw)


def _load_stream(name: str) -> list[OrderIntent]:
    path = FIXTURES / "intents" / name
    intents = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            intents.append(OrderIntent.model_validate(json.loads(line)))
    return intents


def test_runner_determinism(tmp_path):
    """Two runs with identical inputs produce identical summaries."""
    intents = _load_stream("stream_10.jsonl")
    market = _load_market()

    summaries = []
    for i in range(2):
        portfolio = PortfolioState(
            equity=100_000.0,
            start_of_day_equity=100_000.0,
            peak_equity=100_000.0,
            positions={},
        )
        execution = ExecutionState()
        audit_path = tmp_path / f"audit_{i}.jsonl"

        summary, final_p, final_e = run_stream(
            policy_path=POLICY,
            intents=intents,
            portfolio=portfolio,
            execution=execution,
            market=market,
            audit_log_path=audit_path,
        )
        summaries.append(summary.to_dict(final_p, final_e))

    assert summaries[0] == summaries[1], "Non-deterministic run detected"


def test_runner_counts_and_histogram(tmp_path):
    """Runner produces correct decision counts and rule histogram."""
    intents = _load_stream("stream_10.jsonl")
    market = _load_market()
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    execution = ExecutionState()

    summary, final_p, final_e = run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=tmp_path / "audit.jsonl",
    )

    result = summary.to_dict(final_p, final_e)
    assert result["total_intents"] == 10
    assert result["orders_submitted"] == result["decisions"]["ALLOW"] + result["decisions"]["MODIFY"]
    assert result["orders_filled"] <= result["orders_submitted"]
    # At least some orders should be allowed
    assert result["decisions"]["ALLOW"] > 0


def test_runner_positions_update(tmp_path):
    """Portfolio positions are updated after fills."""
    market = _load_market()
    intents = [
        OrderIntent(
            intent_id="pos-001",
            timestamp="2026-02-24T00:00:01Z",
            strategy_id="demo_strategy",
            account_id="acct_1",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=10,
            limit_price=None,
        ),
    ]
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    execution = ExecutionState()

    _, final_p, _ = run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
    )

    assert final_p.positions.get("AAPL") == 10.0


def test_runner_kill_switch_hard_trip(tmp_path):
    """LOSS-002 (drawdown) hard-trips the kill switch via runner state evolution."""
    market = _load_market()

    # First intent triggers drawdown → kill switch
    # Second intent should be denied by KILL-001
    intents = [
        OrderIntent(
            intent_id="kill-001",
            timestamp="2026-02-24T00:00:01Z",
            strategy_id="demo_strategy",
            account_id="acct_1",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=1,
            limit_price=None,
        ),
        OrderIntent(
            intent_id="kill-002",
            timestamp="2026-02-24T00:00:02Z",
            strategy_id="demo_strategy",
            account_id="acct_1",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=1,
            limit_price=None,
        ),
    ]

    # Portfolio with 10% drawdown → triggers LOSS-002
    portfolio = PortfolioState(
        equity=90_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    execution = ExecutionState()

    summary, _, final_e = run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=tmp_path / "audit.jsonl",
    )

    assert final_e.kill_switch_active is True
    # First intent denied (drawdown), second denied (kill switch)
    assert summary.counts["DENY"] == 2
    assert summary.counts["ALLOW"] == 0
    assert "LOSS-002" in summary.rule_histogram
    assert "KILL-001" in summary.rule_histogram


def test_runner_kill_switch_trip_after_n(tmp_path):
    """Kill switch trips after N violations in rolling window.

    base_enforce.yaml: trip_after_n_violations=3, window=300s.
    Send intents that each produce a violation but don't hard-trip.
    After 3 violations accumulate, kill switch should activate.
    """
    market = _load_market()

    # Portfolio near position cap — each buy of 45 shares will trigger EXP-001
    # (45*200 = 9000 = 9% of 100k, but with existing 5 shares → (50*200)/100k = 10%)
    # Actually, let's use the rate limiter. Set execution state near the limit.
    # Strategy rate limit is 10/min. Send 10 intents — the 10th should hit EXEC-002.
    # But we need 3 violations total. Let's construct a scenario:
    #
    # Use position cap violations. With equity=100k, max_position_pct=0.10,
    # max allowed AAPL = 50 shares (50*200=10k=10%).
    # If we already have 50 shares, any buy triggers EXP-001 (MODIFY or DENY).
    # With MODIFY, the allowed_qty would be 0, so it should DENY.

    # Actually simpler: use a portfolio already at the cap.
    # Each buy attempt triggers EXP-001 with allowed_qty=0 → DENY.
    intents = []
    for i in range(4):
        intents.append(
            OrderIntent(
                intent_id=f"tripn-{i:03d}",
                timestamp=f"2026-02-24T00:00:{i+1:02d}Z",
                strategy_id="demo_strategy",
                account_id="acct_1",
                instrument={"symbol": "AAPL", "asset_class": "equity"},
                side="buy",
                order_type="market",
                qty=10,
                limit_price=None,
            )
        )

    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={"AAPL": 50.0},  # already at 10% cap
    )
    execution = ExecutionState()

    summary, _, final_e = run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=tmp_path / "audit.jsonl",
    )

    # Each intent should DENY (position cap breached, allowed_qty=0)
    assert summary.counts["DENY"] == 4
    # Kill switch should have tripped after 3rd intent's violations accumulated
    assert final_e.kill_switch_active is True


def test_runner_audit_log_written(tmp_path):
    """Runner writes one audit event per intent."""
    intents = _load_stream("stream_10.jsonl")
    market = _load_market()
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    execution = ExecutionState()
    audit_path = tmp_path / "audit.jsonl"

    run_stream(
        policy_path=POLICY,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=audit_path,
    )

    lines = [l for l in audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 10


def test_sim_broker_limit_order_rejection():
    """Limit buy below mid price should not fill."""
    from policygate_capital.adapters.sim_broker import SimBrokerAdapter

    broker = SimBrokerAdapter()
    intent = OrderIntent(
        intent_id="limit-001",
        timestamp="2026-02-24T00:00:01Z",
        strategy_id="demo_strategy",
        account_id="acct_1",
        instrument={"symbol": "AAPL", "asset_class": "equity"},
        side="buy",
        order_type="limit",
        qty=10,
        limit_price=190.0,  # below mid of 200
    )
    market = MarketSnapshot(
        timestamp="2026-02-24T00:00:00Z",
        prices={"AAPL": 200.0},
    )
    order_id = broker.submit(intent, market)
    order = broker.get_order(order_id)
    assert order.status == "rejected"
    assert broker.poll_fills() == []


def test_sim_broker_limit_order_fill():
    """Limit buy at or above mid price should fill."""
    from policygate_capital.adapters.sim_broker import SimBrokerAdapter

    broker = SimBrokerAdapter()
    intent = OrderIntent(
        intent_id="limit-002",
        timestamp="2026-02-24T00:00:01Z",
        strategy_id="demo_strategy",
        account_id="acct_1",
        instrument={"symbol": "AAPL", "asset_class": "equity"},
        side="buy",
        order_type="limit",
        qty=10,
        limit_price=210.0,  # above mid of 200
    )
    market = MarketSnapshot(
        timestamp="2026-02-24T00:00:00Z",
        prices={"AAPL": 200.0},
    )
    order_id = broker.submit(intent, market)
    order = broker.get_order(order_id)
    assert order.status == "filled"
    fills = broker.poll_fills()
    assert len(fills) == 1
    assert fills[0].price == 200.0  # fills at mid
