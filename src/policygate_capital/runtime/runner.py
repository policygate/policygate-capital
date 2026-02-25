"""Stream runner: evaluate intents, submit via broker, evolve state.

This is the "runtime governance middleware" — it sits between signal
generation and execution, enforcing capital policy on every order.

The runner owns execution state evolution:
  - Increments order counters after each submission
  - Appends violations to rolling window
  - Trips kill switch on LOSS-002 or after N violations in window
  - Updates portfolio positions after fills

Equity assumption: equity is fixed for the duration of a run. Exposure is
computed against the equity snapshot provided at run start. Cash modelling
and real-time equity updates are out of scope for v0.1.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from policygate_capital.adapters.broker import BrokerAdapter, Fill
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

ExecEventType = Literal[
    "ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED"
]


def _write_exec_event(
    path: Path,
    event_type: ExecEventType,
    intent_id: str,
    order_id: str,
    *,
    run_id: str | None = None,
    policy_hash: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> None:
    """Append a single execution event to the JSONL log."""
    event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "intent_id": intent_id,
        "order_id": order_id,
    }
    if run_id is not None:
        event["run_id"] = run_id
    if policy_hash is not None:
        event["policy_hash"] = policy_hash
    if extra:
        event.update(extra)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


class RunSummary:
    """Accumulates run statistics."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id
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
        d: Dict[str, Any] = {
            "total_intents": self.total,
            "decisions": self.counts,
            "rule_histogram": dict(sorted(self.rule_histogram.items())),
            "orders_submitted": self.submitted,
            "orders_filled": self.filled,
            "final_equity": portfolio.equity,
            "final_positions": dict(sorted(portfolio.positions.items())),
            "kill_switch_active": execution.kill_switch_active,
        }
        if self.run_id is not None:
            d["run_id"] = self.run_id
        return d


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
    broker: Optional[BrokerAdapter] = None,
    exec_log_path: Optional[str | Path] = None,
) -> tuple[RunSummary, PortfolioState, ExecutionState]:
    """Run a stream of intents through the CPE with a broker.

    Args:
        broker: Any BrokerAdapter implementation. Defaults to SimBrokerAdapter
                 when None (deterministic paper trading).
        exec_log_path: Path for execution event JSONL (ORDER_SUBMITTED,
                       ORDER_FILLED, ORDER_REJECTED). Separate from the
                       governance audit log.

    Returns (summary, final_portfolio, final_execution).
    """
    run_id = str(uuid.uuid4())
    engine = PolicyEngine(policy_path)
    if broker is None:
        broker = SimBrokerAdapter()
    summary = RunSummary(run_id=run_id)

    exec_log = Path(exec_log_path) if exec_log_path else None

    policy = engine.policy
    kill_cfg = policy.limits.kill_switch

    for intent in intents:
        # Evaluate
        decision = engine.evaluate(intent, portfolio, market, execution)
        summary.record(decision)

        # Audit (always before submit — survives broker failures)
        if audit_log_path:
            event = build_audit_event(
                decision=decision,
                intent=intent,
                portfolio=portfolio,
                market=market,
                execution=execution,
                policy_hash=engine.policy_hash,
                run_id=run_id,
            )
            write_audit_event(audit_log_path, event)

        # Submit if allowed/modified
        if decision.decision in ("ALLOW", "MODIFY"):
            effective = decision.modified_intent if decision.modified_intent else intent

            # Fail-loud: emit ORDER_REJECTED on broker exception, then re-raise
            try:
                order_id = broker.submit(effective, market)
            except Exception:
                if exec_log:
                    _write_exec_event(
                        exec_log, "ORDER_REJECTED",
                        intent.intent_id, "",
                        run_id=run_id,
                        policy_hash=engine.policy_hash,
                        extra={"symbol": effective.instrument.symbol},
                    )
                raise

            summary.submitted += 1

            if exec_log:
                _write_exec_event(
                    exec_log, "ORDER_SUBMITTED",
                    intent.intent_id, order_id,
                    run_id=run_id,
                    policy_hash=engine.policy_hash,
                    extra={
                        "symbol": effective.instrument.symbol,
                        "side": effective.side,
                        "qty": effective.qty,
                        "order_type": effective.order_type,
                    },
                )

            # Apply fills to portfolio
            fills = broker.poll_fills(since_ts=intent.timestamp)
            for fill in fills:
                _apply_fill(portfolio, fill, market)
                summary.filled += 1

                if exec_log:
                    _write_exec_event(
                        exec_log, "ORDER_FILLED",
                        intent.intent_id, fill.order_id,
                        run_id=run_id,
                        policy_hash=engine.policy_hash,
                        extra={
                            "symbol": fill.symbol,
                            "side": fill.side,
                            "qty": fill.qty,
                            "price": fill.price,
                        },
                    )

            # If submitted but no fills, check for rejection
            if not fills and hasattr(broker, "get_order"):
                bo = broker.get_order(order_id)
                if bo and bo.status == "rejected":
                    if exec_log:
                        _write_exec_event(
                            exec_log, "ORDER_REJECTED",
                            intent.intent_id, order_id,
                            run_id=run_id,
                            policy_hash=engine.policy_hash,
                        )

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
