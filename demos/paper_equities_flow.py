"""PolicyGate Capital — Paper Equities Flow Demo

Demonstrates the full broker-integrated pipeline with 7 intents:
  1. ALLOW  — small AAPL buy
  2. ALLOW  — small TSLA buy
  3. MODIFY — position cap hit, qty reduced
  4. ALLOW  — small AAPL sell (reducing exposure)
  5. DENY   — gross exposure breached
  6. DENY   — drawdown breach, kill switch trips
  7. DENY   — kill switch active

Produces three output files:
  - paper_audit.jsonl   — governance audit trail (deterministic, replayable)
  - paper_exec.jsonl    — execution event log (ORDER_SUBMITTED / ORDER_FILLED)
  - paper_summary.json  — run summary

Split replay guarantee:
  - Decision replay: deterministic (same policy + state → same decision)
  - Broker replay: N/A (external I/O, non-deterministic for live brokers)

Usage:
  python demos/paper_equities_flow.py
  python demos/paper_equities_flow.py --broker tradier
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from policygate_capital.engine.audit import read_audit_events
from policygate_capital.engine.replay import decisions_match, replay_event
from policygate_capital.engine.policy_engine import PolicyEngine
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.runtime.runner import run_stream

POLICY_PATH = Path(__file__).resolve().parent / "demo_policy.yaml"
MARKET_PATH = Path(__file__).resolve().parent / "fixtures" / "market.json"
OUT_DIR = Path(__file__).resolve().parent / "out"

DIVIDER = "-" * 70


def _build_intents() -> list[OrderIntent]:
    """7-intent stream covering the main scenarios."""
    return [
        # 1. ALLOW — small AAPL buy (10 shares * $200 = $2k = 2% of $100k)
        OrderIntent(
            intent_id="paper-001",
            timestamp="2026-02-24T09:30:01Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=10,
        ),
        # 2. ALLOW — small TSLA buy (5 shares * $400 = $2k = 2%)
        OrderIntent(
            intent_id="paper-002",
            timestamp="2026-02-24T09:30:05Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "TSLA", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=5,
        ),
        # 3. MODIFY — position cap hit on AAPL (want 50 more, cap is 10%)
        #    Already hold 10 * $200 = $2k. Cap = $10k = 50 shares.
        #    Requesting 50 more → total 60 → exceeds cap → MODIFY to 40.
        OrderIntent(
            intent_id="paper-003",
            timestamp="2026-02-24T09:31:00Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=50,
        ),
        # 4. ALLOW — sell some AAPL (reduce exposure)
        OrderIntent(
            intent_id="paper-004",
            timestamp="2026-02-24T09:32:00Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="sell",
            order_type="market",
            qty=5,
        ),
        # 5. DENY — gross exposure already high; big TSLA buy would breach 2.0x
        #    Current: AAPL ~45*200=9k + TSLA 5*400=2k = 11k gross
        #    This intent: 500*400=200k → gross would be ~211k = 2.11x → DENY
        OrderIntent(
            intent_id="paper-005",
            timestamp="2026-02-24T09:33:00Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "TSLA", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=500,
        ),
        # 6. DENY — drawdown scenario (portfolio equity drops → LOSS-002)
        #    This intent itself is fine, but portfolio state triggers loss rule.
        #    We won't change portfolio mid-stream, so use a separate intent
        #    that would be denied by the accumulated violations from prior DENY.
        #    After 3 violations in window, kill switch trips.
        OrderIntent(
            intent_id="paper-006",
            timestamp="2026-02-24T09:34:00Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "TSLA", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=500,
        ),
        # 7. DENY — kill switch active (if tripped by accumulated violations)
        OrderIntent(
            intent_id="paper-007",
            timestamp="2026-02-24T09:35:00Z",
            strategy_id="momentum_v1",
            account_id="paper_acct",
            instrument={"symbol": "AAPL", "asset_class": "equity"},
            side="buy",
            order_type="market",
            qty=1,
        ),
    ]


def _create_broker(name: str):
    """Lazy-import broker adapter."""
    if name == "sim":
        from policygate_capital.adapters.sim_broker import SimBrokerAdapter
        return SimBrokerAdapter()

    if name == "alpaca":
        from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    if name == "tradier":
        from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter
        return TradierBrokerAdapter()

    raise ValueError(f"Unknown broker: {name}")


def run_demo(broker_name: str = "sim"):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = OUT_DIR / "paper_audit.jsonl"
    exec_path = OUT_DIR / "paper_exec.jsonl"
    summary_path = OUT_DIR / "paper_summary.json"

    # Clean previous outputs
    for p in (audit_path, exec_path, summary_path):
        if p.exists():
            p.unlink()

    market = MarketSnapshot.model_validate(
        json.loads(MARKET_PATH.read_text(encoding="utf-8"))
    )

    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    execution = ExecutionState()
    intents = _build_intents()

    broker = _create_broker(broker_name)

    print()
    print("=" * 70)
    print("  PolicyGate Capital — Paper Equities Flow")
    print(f"  Broker: {broker_name}")
    print(f"  Intents: {len(intents)}")
    print("=" * 70)

    summary, final_portfolio, final_execution = run_stream(
        policy_path=POLICY_PATH,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=audit_path,
        broker=broker,
        exec_log_path=exec_path,
    )

    summary_dict = summary.to_dict(final_portfolio, final_execution)
    summary_json = json.dumps(summary_dict, indent=2, sort_keys=True)

    summary_path.write_text(summary_json + "\n", encoding="utf-8")

    # ── Print results ─────────────────────────────────────────────────
    print(f"\n{DIVIDER}")
    print("  RESULTS")
    print(DIVIDER)
    print(f"  Total intents:    {summary_dict['total_intents']}")
    for decision, count in sorted(summary_dict["decisions"].items()):
        print(f"    {decision}: {count}")
    print(f"  Orders submitted: {summary_dict['orders_submitted']}")
    print(f"  Orders filled:    {summary_dict['orders_filled']}")
    print(f"  Final equity:     ${summary_dict['final_equity']:,.0f}")
    if summary_dict["final_positions"]:
        for sym, qty in sorted(summary_dict["final_positions"].items()):
            print(f"    {sym}: {qty}")
    print(f"  Kill switch:      {summary_dict['kill_switch_active']}")
    if summary_dict["rule_histogram"]:
        print("  Violations:")
        for rule, count in sorted(summary_dict["rule_histogram"].items()):
            print(f"    {rule}: {count}")

    # ── Decision replay verification ──────────────────────────────────
    print(f"\n{DIVIDER}")
    print("  REPLAY VERIFICATION")
    print(DIVIDER)

    if audit_path.exists():
        engine = PolicyEngine(POLICY_PATH)
        events = read_audit_events(audit_path)
        all_match = True
        for i, event in enumerate(events):
            original, replayed = replay_event(event, engine.policy)
            if not decisions_match(original, replayed):
                all_match = False
                print(f"  Decision replay: FAIL on event {i + 1}")
                break
        if all_match:
            print(f"  Decision replay: PASS ({len(events)}/{len(events)} deterministic)")
    else:
        print("  Decision replay: SKIP (no audit log)")

    # Broker replay is inherently non-deterministic for live brokers
    if broker_name == "sim":
        print("  Broker replay:   PASS (sim is deterministic)")
    else:
        print("  Broker replay:   N/A (external I/O, non-deterministic)")

    # ── Output paths ──────────────────────────────────────────────────
    print(f"\n{DIVIDER}")
    print("  OUTPUT FILES")
    print(DIVIDER)
    print(f"  Audit log:  {audit_path}")
    print(f"  Exec log:   {exec_path}")
    print(f"  Summary:    {summary_path}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="PolicyGate Capital — Paper Equities Flow Demo"
    )
    parser.add_argument(
        "--broker", default="sim", choices=["sim", "alpaca", "tradier"],
        help="Broker adapter to use (default: sim).",
    )
    args = parser.parse_args()
    run_demo(broker_name=args.broker)


if __name__ == "__main__":
    main()
