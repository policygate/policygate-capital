"""Derived metric computation and the deterministic evaluation pipeline.

Evaluation order (fixed, per spec):
  1. Kill switch check
  2. Loss limits (daily loss, drawdown)
  3. Execution throttles (global, per-strategy)
  4. Exposure checks (position, gross, net) — with MODIFY support
  5. If all pass → ALLOW
"""

from __future__ import annotations

from policygate_capital.engine.decisions import Decision, Evidence
from policygate_capital.engine.rules import (
    check_daily_loss,
    check_drawdown,
    check_global_rate,
    check_gross_exposure,
    check_kill_switch,
    check_net_exposure,
    check_position_limit,
    check_strategy_rate,
)
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.policy import CapitalPolicy
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)


def evaluate(
    intent: OrderIntent,
    policy: CapitalPolicy,
    portfolio: PortfolioState,
    market: MarketSnapshot,
    execution: ExecutionState,
) -> Decision:
    """Evaluate an OrderIntent against a CapitalPolicy.

    Returns a deterministic Decision (ALLOW / DENY / MODIFY).
    """
    symbol = intent.instrument.symbol
    violations = []
    evidence = []
    kill_switch_triggered = False

    # --- Fail-closed: missing price ---
    price = market.prices.get(symbol)
    if price is None or price <= 0:
        from policygate_capital.engine.decisions import Violation

        violations.append(
            Violation(
                rule_id="SYS-001",
                severity="CRIT",
                message=f"Missing or invalid price for symbol '{symbol}'.",
                inputs={"symbol": symbol},
                computed={},
            )
        )
        return Decision(
            decision="DENY",
            intent_id=intent.intent_id,
            violations=violations,
            evidence=evidence,
        )

    # --- Derived metrics ---
    equity = portfolio.equity
    sod_equity = portfolio.start_of_day_equity
    peak_equity = portfolio.peak_equity
    current_qty = portfolio.positions.get(symbol, 0.0)

    daily_return = (equity - sod_equity) / sod_equity
    drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0

    if intent.side == "buy":
        new_qty = current_qty + intent.qty
    else:
        new_qty = current_qty - intent.qty

    new_position_value = abs(new_qty * price)
    new_position_pct = new_position_value / equity

    # Compute new gross/net exposure after the trade
    position_values = {
        sym: portfolio.positions.get(sym, 0.0) * market.prices.get(sym, 0.0)
        for sym in set(list(portfolio.positions.keys()) + [symbol])
        if market.prices.get(sym) is not None
    }
    # Update with the proposed new position
    position_values[symbol] = new_qty * price

    gross_exposure = sum(abs(v) for v in position_values.values())
    net_exposure = abs(sum(position_values.values()))
    new_gross_x = gross_exposure / equity if equity > 0 else 0.0
    new_net_x = net_exposure / equity if equity > 0 else 0.0

    evidence.extend(
        [
            Evidence(metric="daily_return", value=round(daily_return, 6), limit=round(-policy.limits.loss.daily_loss_limit_pct, 6)),
            Evidence(metric="drawdown", value=round(drawdown, 6), limit=round(policy.limits.loss.max_drawdown_pct, 6)),
            Evidence(metric="new_position_pct", value=round(new_position_pct, 6), limit=round(policy.resolve_exposure(symbol, intent.strategy_id).max_position_pct, 6)),
            Evidence(metric="gross_exposure_x", value=round(new_gross_x, 6), limit=round(policy.limits.exposure.max_gross_exposure_x, 6)),
            Evidence(metric="net_exposure_x", value=round(new_net_x, 6), limit=round(policy.resolve_exposure(symbol, intent.strategy_id).max_net_exposure_x or 0, 6)),
        ]
    )

    # === 1. Kill switch ===
    v = check_kill_switch(execution.kill_switch_active)
    if v:
        violations.append(v)
        return Decision(
            decision="DENY",
            intent_id=intent.intent_id,
            violations=violations,
            evidence=evidence,
        )

    # === 2. Loss limits ===
    v = check_daily_loss(daily_return, policy.limits.loss.daily_loss_limit_pct)
    if v:
        violations.append(v)

    v = check_drawdown(drawdown, policy.limits.loss.max_drawdown_pct)
    if v:
        violations.append(v)
        # LOSS-002 trips kill switch if configured
        if "LOSS-002" in policy.limits.kill_switch.trip_on_rules:
            kill_switch_triggered = True

    if violations:
        return Decision(
            decision="DENY",
            intent_id=intent.intent_id,
            violations=violations,
            evidence=evidence,
            kill_switch_triggered=kill_switch_triggered,
        )

    # === 3. Execution throttles ===
    exec_limits = policy.resolve_execution(intent.strategy_id)

    v = check_global_rate(execution.orders_last_60s_global, exec_limits)
    if v:
        violations.append(v)

    strategy_orders = execution.orders_last_60s_by_strategy.get(
        intent.strategy_id, 0
    )
    v = check_strategy_rate(strategy_orders, intent.strategy_id, exec_limits)
    if v:
        violations.append(v)

    if violations:
        return Decision(
            decision="DENY",
            intent_id=intent.intent_id,
            violations=violations,
            evidence=evidence,
        )

    # === 4. Exposure checks ===
    exp_limits = policy.resolve_exposure(symbol, intent.strategy_id)

    v_pos, allowed_qty = check_position_limit(
        new_position_pct=new_position_pct,
        requested_qty=intent.qty,
        current_qty=current_qty,
        price=price,
        equity=equity,
        side=intent.side,
        limits=exp_limits,
    )

    v_gross = check_gross_exposure(new_gross_x, exp_limits.max_gross_exposure_x)

    v_net = None
    if exp_limits.max_net_exposure_x is not None:
        v_net = check_net_exposure(new_net_x, exp_limits.max_net_exposure_x)

    if v_pos:
        violations.append(v_pos)
        if allowed_qty is not None and allowed_qty > 0 and not v_gross and not v_net:
            # MODIFY: reduce qty to fit position cap
            modified = intent.model_copy(update={"qty": allowed_qty})
            return Decision(
                decision="MODIFY",
                intent_id=intent.intent_id,
                modified_intent=modified,
                violations=violations,
                evidence=evidence,
            )
        # Cannot modify — hard deny
        if v_gross:
            violations.append(v_gross)
        if v_net:
            violations.append(v_net)
        return Decision(
            decision="DENY",
            intent_id=intent.intent_id,
            violations=violations,
            evidence=evidence,
        )

    if v_gross:
        violations.append(v_gross)
    if v_net:
        violations.append(v_net)

    if violations:
        return Decision(
            decision="DENY",
            intent_id=intent.intent_id,
            violations=violations,
            evidence=evidence,
        )

    # === 5. All passed ===
    return Decision(
        decision="ALLOW",
        intent_id=intent.intent_id,
        violations=[],
        evidence=evidence,
    )
