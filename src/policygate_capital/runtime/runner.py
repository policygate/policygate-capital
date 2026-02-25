"""Stream runner: evaluate intents, submit via broker, evolve state.

This is the "runtime governance middleware" — it sits between signal
generation and execution, enforcing capital policy on every order.

The runner owns execution state evolution:
  - Increments order counters after each submission
  - Appends violations to rolling window
  - Trips kill switch on LOSS-002 or after N violations in window
  - Updates portfolio positions after fills
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from policygate_capital.adapters.broker import Fill
from policygate_capital.adapters.sim_broker import SimBrokerAdapter
from policygate_capital.engine.audit import build_audit_event, write_audit_event
from policygate_capital.engine.decisions import Decision
from policygate_capital.engine.policy_engine import PolicyEngine
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)


class RunSummary:
    """Accumulates run statistics."""

    def __init__(self) -> None:
        self.total: int = 0
        self.counts: Dict[str, int] = {"ALLOW": 0, "MODIFY": 0, "DENY": 0}
        self.rule_histogram: Dict[str, int] = {}
        self.submitted: int = 0
        self.filled: int = 0

    def record(self, decision: Decision) -> None:
        self.total += 1
        self.counts[decision.decision] = self.counts.get(decision.decision, 0) + 1
        for v in decision.violations:
            self.rule_histogram[v.rule_id] = (
                self.rule_histogram.get(v.rule_id, 0) + 1
            )

    def to_dict(
        self,
        portfolio: PortfolioState,
        execution: ExecutionState,
    ) -> Dict[str, Any]:
        return {
            "total_intents": self.total,
            "decisions": self.counts,
            "rule_histogram": dict(sorted(self.rule_histogram.items())),
            "orders_submitted": self.submitted,
            "orders_filled": self.filled,
            "final_equity": portfolio.equity,
            "final_positions": dict(sorted(portfolio.positions.items())),
            "kill_switch_active": execution.kill_switch_active,
        }


def _evict_window(
    violations: List[tuple[str, str]],
    current_ts: str,
    window_seconds: int,
) -> List[tuple[str, str]]:
    """Remove violations outside the rolling window."""
    # Simple string comparison works for RFC 3339 UTC timestamps
    # For v0.1, we use a basic approach: parse the timestamp suffix
    from datetime import datetime, timedelta, timezone

    try:
        now = datetime.fromisoformat(current_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return violations

    cutoff = now - timedelta(seconds=window_seconds)
    result = []
    for ts, rule_id in violations:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t >= cutoff:
                result.append((ts, rule_id))
        except (ValueError, AttributeError):
            result.append((ts, rule_id))
    return result


def run_stream(
    policy_path: str | Path,
    intents: List[OrderIntent],
    portfolio: PortfolioState,
    execution: ExecutionState,
    market: MarketSnapshot,
    audit_log_path: Optional[str | Path] = None,
) -> tuple[RunSummary, PortfolioState, ExecutionState]:
    """Run a stream of intents through the CPE with a sim broker.

    Returns (summary, final_portfolio, final_execution).
    """
    engine = PolicyEngine(policy_path)
    broker = SimBrokerAdapter()
    summary = RunSummary()

    policy = engine.policy
    kill_cfg = policy.limits.kill_switch

    for intent in intents:
        # Evaluate
        decision = engine.evaluate(intent, portfolio, market, execution)
        summary.record(decision)

        # Audit
        if audit_log_path:
            event = build_audit_event(
                decision=decision,
                intent=intent,
                portfolio=portfolio,
                market=market,
                execution=execution,
                policy_hash=engine.policy_hash,
            )
            write_audit_event(audit_log_path, event)

        # Submit if allowed/modified
        if decision.decision in ("ALLOW", "MODIFY"):
            effective = decision.modified_intent if decision.modified_intent else intent
            order_id = broker.submit(effective, market)
            summary.submitted += 1

            # Apply fills to portfolio
            fills = broker.poll_fills(since_ts=intent.timestamp)
            for fill in fills:
                _apply_fill(portfolio, fill, market)
                summary.filled += 1

            # Update execution counters
            execution.orders_last_60s_global += 1
            strat_orders = execution.orders_last_60s_by_strategy.get(
                intent.strategy_id, 0
            )
            execution.orders_last_60s_by_strategy[intent.strategy_id] = (
                strat_orders + 1
            )

        # Record violations in rolling window
        for v in decision.violations:
            execution.violations_last_window.append(
                (intent.timestamp, v.rule_id)
            )

        # Evict old violations from window
        execution.violations_last_window = _evict_window(
            execution.violations_last_window,
            intent.timestamp,
            kill_cfg.violation_window_seconds,
        )

        # Trip kill switch if decision says so (hard trip via LOSS-002)
        if decision.kill_switch_triggered:
            execution.kill_switch_active = True

        # Trip kill switch after N violations in rolling window
        if (
            not execution.kill_switch_active
            and len(execution.violations_last_window)
            >= kill_cfg.trip_after_n_violations
        ):
            execution.kill_switch_active = True

    return summary, portfolio, execution


def _apply_fill(
    portfolio: PortfolioState,
    fill: Fill,
    market: MarketSnapshot,
) -> None:
    """Update portfolio positions after a fill."""
    current_qty = portfolio.positions.get(fill.symbol, 0.0)
    if fill.side == "buy":
        new_qty = current_qty + fill.qty
    else:
        new_qty = current_qty - fill.qty

    if abs(new_qty) < 1e-10:
        portfolio.positions.pop(fill.symbol, None)
    else:
        portfolio.positions[fill.symbol] = new_qty

    # Recompute unrealized PnL (simple: sum of position_value - cost basis)
    # For v0.1, we don't track cost basis — just update positions.
    # Equity stays constant (no cash modeling in v0.1).
