"""Drawdown Crash scenario — portfolio with 8% drawdown, all intents denied.

Demonstrates LOSS-002 hard trip on the first intent (drawdown 8% > 5%
threshold), kill switch activation, and all subsequent intents denied
by KILL-001.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.runtime.runner import run_stream

SCENARIO_DIR = Path(__file__).resolve().parent
OUT_DIR = SCENARIO_DIR / "out"


def load_intents() -> list[OrderIntent]:
    intents = []
    for line in (SCENARIO_DIR / "intents.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            intents.append(OrderIntent.model_validate(json.loads(line)))
    return intents


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = OUT_DIR / "audit.jsonl"
    exec_path = OUT_DIR / "exec.jsonl"
    summary_path = OUT_DIR / "summary.json"

    for p in (audit_path, exec_path, summary_path):
        if p.exists():
            p.unlink()

    market = MarketSnapshot.model_validate(
        json.loads((SCENARIO_DIR / "market.json").read_text(encoding="utf-8"))
    )
    portfolio = PortfolioState.model_validate(
        json.loads((SCENARIO_DIR / "portfolio.json").read_text(encoding="utf-8"))
    )
    execution = ExecutionState()
    intents = load_intents()

    summary, final_p, final_e = run_stream(
        policy_path=SCENARIO_DIR / "policy.yaml",
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=audit_path,
        exec_log_path=exec_path,
    )

    summary_dict = summary.to_dict(final_p, final_e)
    summary_path.write_text(
        json.dumps(summary_dict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Drawdown Crash — {summary_dict['total_intents']} intents")
    for dec, count in sorted(summary_dict["decisions"].items()):
        print(f"  {dec}: {count}")
    print(f"  Kill switch: {summary_dict['kill_switch_active']}")
    if summary_dict["rule_histogram"]:
        print("  Violations:")
        for rule, count in sorted(summary_dict["rule_histogram"].items()):
            print(f"    {rule}: {count}")
    print(f"  Outputs: {OUT_DIR}")


if __name__ == "__main__":
    main()
