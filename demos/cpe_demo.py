"""PolicyGate Capital — End-to-End Demo

Demonstrates the full CPE enforcement pipeline:
  Step 1: ALLOW   — small trade passes all checks
  Step 2: MODIFY  — position cap exceeded, qty reduced to fit
  Step 3: DENY    — gross exposure breached
  Step 4: DENY    — drawdown breached, kill switch trips
  Step 5: DENY    — kill switch active, immediate rejection

Produces an append-only audit log and replays it for determinism proof.

Usage:
  python demos/cpe_demo.py
  python demos/cpe_demo.py --audit-log demos/out/demo_audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the src directory is importable when running from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from policygate_capital.engine.audit import (
    build_audit_event,
    read_audit_events,
    write_audit_event,
)
from policygate_capital.engine.policy_engine import PolicyEngine
from policygate_capital.engine.replay import decisions_match, replay_event
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
POLICY_PATH = Path(__file__).resolve().parent / "demo_policy.yaml"

DIVIDER = "-" * 70


def load_intent(name: str) -> OrderIntent:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return OrderIntent.model_validate(raw)


def print_step(step: int, title: str, intent: OrderIntent, decision, portfolio: PortfolioState):
    print(f"\n{DIVIDER}")
    print(f"  STEP {step}: {title}")
    print(DIVIDER)
    print(f"  Intent:    {intent.side.upper()} {intent.qty} {intent.instrument.symbol} ({intent.order_type})")
    print(f"  Equity:    ${portfolio.equity:,.0f}  |  SOD: ${portfolio.start_of_day_equity:,.0f}  |  Peak: ${portfolio.peak_equity:,.0f}")
    if portfolio.positions:
        pos_str = ", ".join(f"{sym}: {qty}" for sym, qty in portfolio.positions.items())
        print(f"  Positions: {pos_str}")
    print()
    print(f"  Decision:  {decision.decision}")
    if decision.violations:
        for v in decision.violations:
            print(f"  Violation: [{v.rule_id}] {v.severity} — {v.message}")
    if decision.modified_intent:
        print(f"  Modified:  qty {intent.qty} -> {decision.modified_intent.qty}")
    if decision.kill_switch_triggered:
        print(f"  ** KILL SWITCH TRIGGERED **")
    print()


def run_demo(audit_log_path: Path | None = None):
    engine = PolicyEngine(POLICY_PATH)

    market = MarketSnapshot.model_validate(
        json.loads((FIXTURES / "market.json").read_text(encoding="utf-8"))
    )

    # Clean audit log
    if audit_log_path and audit_log_path.exists():
        audit_log_path.unlink()

    print()
    print("=" * 70)
    print("  PolicyGate Capital — CPE Demo")
    print(f"  Policy: {POLICY_PATH.name}  (hash: {engine.policy_hash[:16]}...)")
    print(f"  Market: AAPL=${market.prices['AAPL']:.0f}, TSLA=${market.prices['TSLA']:.0f}")
    print("=" * 70)

    results = []

    # ── Step 1: ALLOW — small trade ──────────────────────────────────────
    intent = load_intent("intent_step1_allow.json")
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    execution = ExecutionState()

    decision = engine.evaluate(intent, portfolio, market, execution)
    print_step(1, "ALLOW — small trade", intent, decision, portfolio)
    assert decision.decision == "ALLOW", f"Expected ALLOW, got {decision.decision}"
    results.append((intent, portfolio, execution, decision))

    # ── Step 2: MODIFY — position cap exceeded ───────────────────────────
    intent = load_intent("intent_step2_modify.json")
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={"AAPL": 10.0},  # from step 1 fill
    )
    execution = ExecutionState()

    decision = engine.evaluate(intent, portfolio, market, execution)
    print_step(2, "MODIFY — position cap exceeded (EXP-001)", intent, decision, portfolio)
    assert decision.decision == "MODIFY", f"Expected MODIFY, got {decision.decision}"
    assert decision.modified_intent.qty < intent.qty
    results.append((intent, portfolio, execution, decision))

    # ── Step 3: DENY — gross exposure breached ───────────────────────────
    intent = load_intent("intent_step3_deny_gross.json")
    portfolio = PortfolioState(
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={"AAPL": 600.0, "TSLA": 300.0},
        # AAPL: 600*200=120k, TSLA: 300*400=120k → gross=240k → 2.4x > 2.0x
    )
    execution = ExecutionState()

    decision = engine.evaluate(intent, portfolio, market, execution)
    print_step(3, "DENY — gross exposure breached (EXP-002)", intent, decision, portfolio)
    assert decision.decision == "DENY", f"Expected DENY, got {decision.decision}"
    results.append((intent, portfolio, execution, decision))

    # ── Step 4: DENY — drawdown breached, kill switch trips ──────────────
    intent = load_intent("intent_step4_drawdown.json")
    portfolio = PortfolioState(
        equity=90_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
        realized_pnl_today=-10_000.0,
    )
    execution = ExecutionState()

    decision = engine.evaluate(intent, portfolio, market, execution)
    print_step(4, "DENY — drawdown breached, kill switch trips (LOSS-002)", intent, decision, portfolio)
    assert decision.decision == "DENY", f"Expected DENY, got {decision.decision}"
    assert decision.kill_switch_triggered, "Expected kill_switch_triggered=True"
    results.append((intent, portfolio, execution, decision))

    # ── Step 5: DENY — kill switch active ────────────────────────────────
    intent = load_intent("intent_step5_killed.json")
    portfolio = PortfolioState(
        equity=90_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        positions={},
    )
    # Kill switch was tripped in step 4 — now active
    execution = ExecutionState(kill_switch_active=True)

    decision = engine.evaluate(intent, portfolio, market, execution)
    print_step(5, "DENY — kill switch active (KILL-001)", intent, decision, portfolio)
    assert decision.decision == "DENY", f"Expected DENY, got {decision.decision}"
    assert any(v.rule_id == "KILL-001" for v in decision.violations)
    results.append((intent, portfolio, execution, decision))

    # ── Audit log ────────────────────────────────────────────────────────
    if audit_log_path:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        for intent_i, port_i, exec_i, dec_i in results:
            event = build_audit_event(
                decision=dec_i,
                intent=intent_i,
                portfolio=port_i,
                market=market,
                execution=exec_i,
                policy_hash=engine.policy_hash,
            )
            write_audit_event(audit_log_path, event)
        print(f"{DIVIDER}")
        print(f"  Audit log: {audit_log_path} ({len(results)} events)")

    # ── Replay verification ──────────────────────────────────────────────
    if audit_log_path and audit_log_path.exists():
        events = read_audit_events(audit_log_path)
        all_match = True
        for i, event in enumerate(events):
            original, replayed = replay_event(event, engine.policy)
            match = decisions_match(original, replayed)
            if not match:
                all_match = False
                print(f"  Replay MISMATCH on event {i + 1}")
        if all_match:
            print(f"  Replay:    {len(events)}/{len(events)} events reproduced deterministically")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{DIVIDER}")
    print("  SUMMARY")
    print(DIVIDER)
    counts = {}
    for _, _, _, d in results:
        counts[d.decision] = counts.get(d.decision, 0) + 1
    for verdict, count in sorted(counts.items()):
        print(f"    {verdict}: {count}")
    rule_counts = {}
    for _, _, _, d in results:
        for v in d.violations:
            rule_counts[v.rule_id] = rule_counts.get(v.rule_id, 0) + 1
    if rule_counts:
        print("  Violations:")
        for rule_id, count in sorted(rule_counts.items()):
            print(f"    {rule_id}: {count}")
    print()


def main():
    parser = argparse.ArgumentParser(description="PolicyGate Capital — CPE Demo")
    parser.add_argument(
        "--audit-log",
        default=str(Path(__file__).resolve().parent / "out" / "demo_audit.jsonl"),
        help="Path to write JSONL audit log.",
    )
    args = parser.parse_args()
    run_demo(audit_log_path=Path(args.audit_log))


if __name__ == "__main__":
    main()
