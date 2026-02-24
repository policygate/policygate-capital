# PolicyGate Capital

Deterministic runtime capital governance for autonomous trading systems.

PolicyGate Capital is a policy engine that evaluates proposed orders against capital constraints and returns **ALLOW**, **DENY**, or **MODIFY** decisions. It enforces position limits, exposure caps, loss limits, execution throttles, and kill switches — deterministically, with an append-only audit trail.

## Install

```bash
pip install policygate-capital
```

## Quick Start

### Define a policy

```yaml
# policy.yaml
version: "0.1"
timezone: "UTC"

defaults:
  mode: "enforce"    # or "monitor" (log violations, always ALLOW)
  decision: "deny"   # fail-closed on evaluation errors

limits:
  exposure:
    max_position_pct: 0.10        # 10% of equity per symbol
    max_gross_exposure_x: 2.0     # 2x equity total
    max_net_exposure_x: 1.0       # 1x equity net
  loss:
    daily_loss_limit_pct: 0.02    # -2% daily loss triggers deny
    max_drawdown_pct: 0.05        # -5% drawdown triggers kill switch
  execution:
    max_orders_per_minute_global: 20
    max_orders_per_minute_by_strategy: 10
  kill_switch:
    trip_on_rules: ["LOSS-002"]
    trip_after_n_violations: 3
    violation_window_seconds: 300

overrides:
  symbols:
    AAPL:
      exposure:
        max_position_pct: 0.05    # tighter limit for AAPL
        max_gross_exposure_x: 2.0
        max_net_exposure_x: 1.0
  strategies: {}
```

### Use as a library

```python
from policygate_capital.engine.policy_engine import PolicyEngine
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState, MarketSnapshot, PortfolioState,
)

engine = PolicyEngine("policy.yaml")

intent = OrderIntent(
    intent_id="order-001",
    timestamp="2026-02-18T00:00:00Z",
    strategy_id="momentum_v1",
    account_id="acct_1",
    instrument={"symbol": "AAPL", "asset_class": "equity"},
    side="buy",
    order_type="market",
    qty=100,
    limit_price=None,
)

portfolio = PortfolioState(
    equity=100000.0,
    start_of_day_equity=100000.0,
    peak_equity=100000.0,
    positions={},
)

market = MarketSnapshot(
    timestamp="2026-02-18T00:00:00Z",
    prices={"AAPL": 200.0},
)

execution = ExecutionState()

decision = engine.evaluate(intent, portfolio, market, execution)
print(decision.decision)       # "MODIFY" — reduced to fit 10% cap
print(decision.modified_intent.qty)  # 50.0
```

### Use as a CLI

```bash
policygate-eval \
  --policy policy.yaml \
  --intent intent.json \
  --portfolio portfolio.json \
  --market market.json \
  --execution execution.json \
  --audit-log audit.jsonl \
  --pretty
```

Exit codes: `0` = ALLOW/MODIFY, `1` = DENY, `2` = error.

## Rules

| Rule | Domain | Severity | Trigger |
|------|--------|----------|---------|
| KILL-001 | Kill switch | CRIT | Kill switch active |
| LOSS-001 | Loss | HIGH | Daily return breaches limit |
| LOSS-002 | Loss | CRIT | Drawdown breaches limit (trips kill switch) |
| EXEC-001 | Execution | HIGH | Global order rate exceeded |
| EXEC-002 | Execution | HIGH | Per-strategy order rate exceeded |
| EXP-001 | Exposure | HIGH | Position size breaches limit (MODIFY if possible) |
| EXP-002 | Exposure | HIGH | Gross exposure breaches limit |
| EXP-003 | Exposure | HIGH | Net exposure breaches limit |
| SYS-001 | System | CRIT | Missing/invalid price (fail-closed) |

## Evaluation Order

Fixed and deterministic:

1. **SYS-001** — Missing data check (fail-closed)
2. **KILL-001** — Kill switch
3. **LOSS-001/002** — Loss limits (daily loss, drawdown)
4. **EXEC-001/002** — Execution throttles
5. **EXP-001/002/003** — Exposure checks (with MODIFY for position cap)

## Monitor Mode

Set `defaults.mode: "monitor"` to log all violations without blocking orders. Useful for shadow-running policies against live traffic before enforcement.

SYS-001 (missing price) still denies in monitor mode — you can't evaluate without data.

## Audit Trail

Every evaluation emits an append-only JSONL event containing:
- Original intent, portfolio state, market snapshot, execution state
- Decision with all violations and computed evidence
- SHA-256 hash of the policy file
- Engine version and timestamp

Audit events can be replayed to verify determinism:

```python
from policygate_capital.engine.replay import replay_event, decisions_match

original, replayed = replay_event(event, policy)
assert decisions_match(original, replayed)
```

## License

MIT
