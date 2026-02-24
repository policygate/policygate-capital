"""Individual rule functions.

Each rule receives the evaluation context and returns a Violation if triggered,
or None if the rule passes. Rules are pure functions — no side effects.
"""

from __future__ import annotations

from typing import Optional

from policygate_capital.engine.decisions import Severity, Violation
from policygate_capital.models.policy import ExposureLimits, ExecutionLimits


def check_kill_switch(kill_switch_active: bool) -> Optional[Violation]:
    """KILL-001: kill switch is active."""
    if kill_switch_active:
        return Violation(
            rule_id="KILL-001",
            severity="CRIT",
            message="Kill switch is active — all orders denied.",
            inputs={"kill_switch_active": True},
            computed={},
        )
    return None


def check_daily_loss(
    daily_return: float, limit_pct: float
) -> Optional[Violation]:
    """LOSS-001: daily loss limit breached."""
    if daily_return <= -limit_pct:
        return Violation(
            rule_id="LOSS-001",
            severity="HIGH",
            message=(
                f"Daily loss {daily_return:.4f} breaches "
                f"limit -{limit_pct:.4f}."
            ),
            inputs={"daily_loss_limit_pct": limit_pct},
            computed={"daily_return": daily_return},
        )
    return None


def check_drawdown(
    drawdown: float, limit_pct: float
) -> Optional[Violation]:
    """LOSS-002: max drawdown breached (also trips kill switch)."""
    if drawdown >= limit_pct:
        return Violation(
            rule_id="LOSS-002",
            severity="CRIT",
            message=(
                f"Drawdown {drawdown:.4f} breaches "
                f"limit {limit_pct:.4f}."
            ),
            inputs={"max_drawdown_pct": limit_pct},
            computed={"drawdown": drawdown},
        )
    return None


def check_global_rate(
    orders_last_60s: int, limits: ExecutionLimits
) -> Optional[Violation]:
    """EXEC-001: global order rate limit breached."""
    if orders_last_60s >= limits.max_orders_per_minute_global:
        return Violation(
            rule_id="EXEC-001",
            severity="HIGH",
            message=(
                f"Global rate {orders_last_60s} orders/min "
                f"exceeds limit {limits.max_orders_per_minute_global}."
            ),
            inputs={
                "max_orders_per_minute_global": limits.max_orders_per_minute_global,
            },
            computed={"orders_last_60s_global": orders_last_60s},
        )
    return None


def check_strategy_rate(
    orders_last_60s: int, strategy_id: str, limits: ExecutionLimits
) -> Optional[Violation]:
    """EXEC-002: per-strategy order rate limit breached."""
    if orders_last_60s >= limits.max_orders_per_minute_by_strategy:
        return Violation(
            rule_id="EXEC-002",
            severity="HIGH",
            message=(
                f"Strategy '{strategy_id}' rate {orders_last_60s} orders/min "
                f"exceeds limit {limits.max_orders_per_minute_by_strategy}."
            ),
            inputs={
                "strategy_id": strategy_id,
                "max_orders_per_minute_by_strategy": limits.max_orders_per_minute_by_strategy,
            },
            computed={"orders_last_60s_strategy": orders_last_60s},
        )
    return None


def check_position_limit(
    new_position_pct: float,
    requested_qty: float,
    current_qty: float,
    price: float,
    equity: float,
    side: str,
    limits: ExposureLimits,
) -> tuple[Optional[Violation], Optional[float]]:
    """EXP-001: per-symbol position limit.

    Returns (violation_or_none, modified_qty_or_none).
    If the position breaches the limit, computes a reduced qty that fits.
    """
    if new_position_pct <= limits.max_position_pct:
        return None, None

    # Compute max allowed qty change
    max_value = limits.max_position_pct * equity
    if side == "buy":
        max_new_qty = max_value / price
        allowed_delta = max_new_qty - current_qty
    else:
        max_new_qty = -(max_value / price)
        allowed_delta = current_qty - (-max_new_qty)

    allowed_delta = max(allowed_delta, 0.0)

    violation = Violation(
        rule_id="EXP-001",
        severity="HIGH",
        message=(
            f"Position {new_position_pct:.4f} breaches "
            f"limit {limits.max_position_pct:.4f}."
        ),
        inputs={"max_position_pct": limits.max_position_pct},
        computed={
            "new_position_pct": new_position_pct,
            "requested_qty": requested_qty,
            "allowed_qty": round(allowed_delta, 8),
        },
    )
    return violation, round(allowed_delta, 8)


def check_gross_exposure(
    new_gross_x: float, limit_x: float
) -> Optional[Violation]:
    """EXP-002: gross exposure limit breached."""
    if new_gross_x > limit_x:
        return Violation(
            rule_id="EXP-002",
            severity="HIGH",
            message=(
                f"Gross exposure {new_gross_x:.4f}x breaches "
                f"limit {limit_x:.4f}x."
            ),
            inputs={"max_gross_exposure_x": limit_x},
            computed={"gross_exposure_x": new_gross_x},
        )
    return None


def check_net_exposure(
    new_net_x: float, limit_x: float
) -> Optional[Violation]:
    """EXP-003: net exposure limit breached."""
    if new_net_x > limit_x:
        return Violation(
            rule_id="EXP-003",
            severity="HIGH",
            message=(
                f"Net exposure {new_net_x:.4f}x breaches "
                f"limit {limit_x:.4f}x."
            ),
            inputs={"max_net_exposure_x": limit_x},
            computed={"net_exposure_x": new_net_x},
        )
    return None
