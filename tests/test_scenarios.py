"""Tests for the scenario packs.

Each test loads scenario inputs, runs via run_stream(), and asserts on
the summary. All outputs go to tmp_path (no repo writes).
"""

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

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def _load_scenario(name: str):
    """Load inputs for a named scenario. Returns (policy_path, intents, portfolio, market)."""
    d = SCENARIOS_DIR / name
    policy_path = d / "policy.yaml"
    market = MarketSnapshot.model_validate(
        json.loads((d / "market.json").read_text(encoding="utf-8"))
    )
    portfolio = PortfolioState.model_validate(
        json.loads((d / "portfolio.json").read_text(encoding="utf-8"))
    )
    intents = []
    for line in (d / "intents.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            intents.append(OrderIntent.model_validate(json.loads(line)))
    return policy_path, intents, portfolio, market


def _run_scenario(name: str, tmp_path):
    """Run a scenario and return (summary_dict, final_execution)."""
    policy_path, intents, portfolio, market = _load_scenario(name)
    execution = ExecutionState()

    summary, final_p, final_e = run_stream(
        policy_path=policy_path,
        intents=intents,
        portfolio=portfolio,
        execution=execution,
        market=market,
        audit_log_path=tmp_path / "audit.jsonl",
        exec_log_path=tmp_path / "exec.jsonl",
    )
    return summary.to_dict(final_p, final_e), final_e


def test_normal_day_scenario(tmp_path):
    """Normal day: mix of ALLOW, MODIFY, DENY."""
    result, final_e = _run_scenario("normal_day", tmp_path)
    assert result["total_intents"] == 10
    assert result["decisions"]["ALLOW"] > 0
    assert result["decisions"]["MODIFY"] > 0
    assert result["decisions"]["DENY"] > 0
    # Verify audit and exec logs were written
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([l for l in audit if l.strip()]) == 10


def test_throttle_burst_scenario(tmp_path):
    """Throttle burst: EXEC violations accumulate, kill switch trips."""
    result, final_e = _run_scenario("throttle_burst", tmp_path)
    assert result["total_intents"] == 25
    # Tight rate limits → mostly DENY
    assert result["decisions"]["DENY"] > result["decisions"]["ALLOW"]
    # EXEC-002 (strategy rate) should fire
    assert "EXEC-002" in result["rule_histogram"]
    # Kill switch should trip from accumulated violations
    assert final_e.kill_switch_active is True
    assert result["kill_switch_active"] is True


def test_drawdown_crash_scenario(tmp_path):
    """Drawdown crash: LOSS-002 hard trip, all intents denied."""
    result, final_e = _run_scenario("drawdown_crash", tmp_path)
    assert result["total_intents"] == 5
    # All denied — drawdown breach on first intent, kill switch on rest
    assert result["decisions"]["ALLOW"] == 0
    assert result["decisions"]["MODIFY"] == 0
    assert result["decisions"]["DENY"] == 5
    assert result["orders_submitted"] == 0
    # LOSS-002 fires on every intent (drawdown is persistent)
    assert "LOSS-002" in result["rule_histogram"]
    # Kill switch tripped by LOSS-002
    assert "KILL-001" in result["rule_histogram"]
    assert final_e.kill_switch_active is True
