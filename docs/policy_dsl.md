# Policy DSL Reference

## Overview

The Capital Policy is defined in a YAML file and validated by strict Pydantic models. Unknown fields are rejected. All numeric limits have explicit bounds.

## Full Schema

```yaml
version: "0.1"          # Required. Only "0.1" accepted.
timezone: "UTC"          # Required. Only "UTC" in v0.1.

defaults:
  mode: "enforce"        # "enforce" | "monitor"
  decision: "deny"       # Default decision when no rules fire: "deny" | "allow"

limits:
  exposure:
    max_position_pct: 0.10       # (0, 1] — max single-symbol position as % of equity
    max_gross_exposure_x: 2.0    # (0, ∞) — max gross exposure as multiple of equity
    max_net_exposure_x: 1.0      # (0, ∞) or null — max net exposure; null = unchecked

  loss:
    daily_loss_limit_pct: 0.02   # (0, 1] — max allowed daily loss
    max_drawdown_pct: 0.05       # (0, 1] — max drawdown from peak equity

  execution:
    max_orders_per_minute_global: 20          # [1, 10000]
    max_orders_per_minute_by_strategy: 10     # [1, 10000]

  kill_switch:
    trip_on_rules: ["LOSS-002"]              # Rules that hard-trip the kill switch
    trip_after_n_violations: 3                # [1, 10000] — trip after N violations in window
    violation_window_seconds: 300             # [1, 31536000] — rolling window size

overrides:
  symbols:
    TSLA:                                     # Per-symbol override
      exposure:
        max_position_pct: 0.05
        max_gross_exposure_x: 1.5
        max_net_exposure_x: 0.8
      loss:                                   # Optional — falls back to defaults if omitted
        daily_loss_limit_pct: 0.01
        max_drawdown_pct: 0.03
      execution:                              # Optional
        max_orders_per_minute_global: 10
        max_orders_per_minute_by_strategy: 5

  strategies:
    aggressive_alpha:                         # Per-strategy override
      exposure:
        max_position_pct: 0.15
        max_gross_exposure_x: 3.0
        max_net_exposure_x: 2.0
```

## Field Reference

### `defaults`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `"enforce"` \| `"monitor"` | `"enforce"` | Monitor mode logs violations but always ALLOWs (except SYS-001) |
| `decision` | `"deny"` \| `"allow"` | `"deny"` | Fail-closed default |

### `limits.exposure`

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `max_position_pct` | float | (0, 1] | Max single-symbol position as fraction of equity |
| `max_gross_exposure_x` | float | (0, +inf) | Max sum of absolute position values / equity |
| `max_net_exposure_x` | float \| null | (0, +inf) | Max absolute net exposure / equity. `null` = skip check |

### `limits.loss`

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `daily_loss_limit_pct` | float | (0, 1] | Triggers LOSS-001 when `(equity - sod_equity) / sod_equity <= -limit` |
| `max_drawdown_pct` | float | (0, 1] | Triggers LOSS-002 when `(peak - equity) / peak >= limit` |

### `limits.execution`

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `max_orders_per_minute_global` | int | [1, 10000] | EXEC-001 fires when global count >= limit |
| `max_orders_per_minute_by_strategy` | int | [1, 10000] | EXEC-002 fires when strategy count >= limit |

### `limits.kill_switch`

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `trip_on_rules` | list[str] | — | Rule IDs that hard-trip the kill switch (e.g., `["LOSS-002"]`) |
| `trip_after_n_violations` | int | [1, 10000] | Trip after this many violations in the rolling window |
| `violation_window_seconds` | int | [1, 31536000] | Rolling window for violation counting |

### `overrides`

Override blocks follow the same schema as the default limits but are **optional**. If an override section is omitted, the default limits apply.

**Precedence**: symbol override > strategy override > defaults.

Example: If both `symbols.TSLA.exposure` and `strategies.aggressive_alpha.exposure` are defined, an intent for TSLA from `aggressive_alpha` uses the **symbol** override.

## Rules Reference

| Rule ID | Category | Severity | Description |
|---------|----------|----------|-------------|
| SYS-001 | System | CRIT | Missing or invalid price → fail-closed DENY |
| KILL-001 | Kill Switch | CRIT | Kill switch active → DENY all |
| LOSS-001 | Loss | HIGH | Daily loss limit breached |
| LOSS-002 | Loss | CRIT | Max drawdown breached (hard-trips kill switch) |
| EXEC-001 | Execution | HIGH | Global order rate limit exceeded |
| EXEC-002 | Execution | HIGH | Per-strategy order rate limit exceeded |
| EXP-001 | Exposure | HIGH | Position cap breached (supports MODIFY) |
| EXP-002 | Exposure | HIGH | Gross exposure limit exceeded |
| EXP-003 | Exposure | HIGH | Net exposure limit exceeded |

## Validation

- All fields use Pydantic `ConfigDict(extra="forbid")` — typos or unknown keys cause a validation error.
- The `version` field only accepts `"0.1"`.
- The `timezone` field only accepts `"UTC"` in v0.1.
- Numeric bounds are enforced at parse time (e.g., `max_position_pct` must be in `(0, 1]`).

## Example: Minimal Policy

```yaml
version: "0.1"
timezone: "UTC"

limits:
  exposure:
    max_position_pct: 0.10
    max_gross_exposure_x: 2.0
  loss:
    daily_loss_limit_pct: 0.02
    max_drawdown_pct: 0.05
  execution:
    max_orders_per_minute_global: 20
    max_orders_per_minute_by_strategy: 10
  kill_switch:
    trip_on_rules: ["LOSS-002"]
    trip_after_n_violations: 3
    violation_window_seconds: 300
```
