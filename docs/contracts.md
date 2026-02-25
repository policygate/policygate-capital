# Event Contracts

Stable schemas for PolicyGate Capital's governance and execution events.
JSON Schema definitions live in [`docs/schemas/`](schemas/).

## Stability Policy

- **Append-only within a major version.** New fields may be added; existing
  fields will not be removed or change type.
- Breaking changes (field removal, type change, semantic change) require a
  major version bump.
- Optional fields (not in `required`) may appear or be absent in any event.

---

## AuditEvent

One JSONL line per evaluated intent. Self-contained: includes the full intent,
portfolio state, market snapshot, execution state, and decision. Produced by
`build_audit_event()` in `engine/audit.py`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | string (UUID) | yes | Unique event identifier |
| `timestamp` | string (ISO 8601) | yes | UTC wall-clock time of evaluation |
| `engine_version` | string | yes | PolicyGate Capital version |
| `policy_hash` | string (64 chars) | yes | SHA-256 of the policy YAML |
| `run_id` | string (UUID) | no | Shared across all events in a `run_stream()` invocation |
| `intent` | OrderIntent | yes | The order intent evaluated |
| `portfolio_state` | PortfolioState | yes | Portfolio at decision time |
| `market_snapshot` | MarketSnapshot | yes | Market prices at decision time |
| `execution_state` | ExecutionState | yes | Execution counters at decision time |
| `decision` | Decision | yes | The governance verdict |

Schema: [`audit_event.schema.json`](schemas/audit_event.schema.json)

---

## ExecutionEvent

One JSONL line per broker interaction. Emitted by `_write_exec_event()` in
`runtime/runner.py`. Separate from the audit log — this tracks what happened
at the broker, not what was decided.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ts` | string (ISO 8601) | yes | UTC wall-clock time |
| `event` | enum | yes | `ORDER_SUBMITTED`, `ORDER_FILLED`, or `ORDER_REJECTED` |
| `intent_id` | string | yes | Links to audit event |
| `order_id` | string | yes | Broker-assigned order ID (empty on submit failure) |
| `run_id` | string (UUID) | no | Same as audit event `run_id` |
| `policy_hash` | string | no | Same as audit event `policy_hash` |
| `symbol` | string | no | Instrument symbol |
| `side` | string | no | `buy` or `sell` |
| `qty` | number | no | Order/fill quantity |
| `order_type` | string | no | `market` or `limit` (SUBMITTED only) |
| `price` | number | no | Fill price (FILLED only) |

Schema: [`execution_event.schema.json`](schemas/execution_event.schema.json)

### Event lifecycle

```
Intent ALLOW/MODIFY
  ├─ broker.submit() succeeds
  │   ├─ ORDER_SUBMITTED
  │   ├─ broker.poll_fills()
  │   │   ├─ ORDER_FILLED (per fill)
  │   │   └─ (or) get_order() → rejected → ORDER_REJECTED
  │   └─ (no fills, no rejection → no further events)
  └─ broker.submit() throws
      └─ ORDER_REJECTED (order_id="", exception re-raised)
```

---

## Decision

The governance verdict for a single intent.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `decision` | enum | yes | `ALLOW`, `MODIFY`, or `DENY` |
| `intent_id` | string | yes | The evaluated intent |
| `modified_intent` | OrderIntent \| null | yes | Reduced-qty intent (MODIFY only) |
| `violations` | Violation[] | yes | Rule violations detected |
| `evidence` | Evidence[] | yes | Computed metrics vs limits |
| `kill_switch_triggered` | boolean | yes | Whether this decision trips the kill switch |

Schema: [`decision.schema.json`](schemas/decision.schema.json)

---

## RunSummary

End-of-run aggregate statistics. Produced by `RunSummary.to_dict()`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `total_intents` | integer | yes | Number of intents evaluated |
| `decisions` | object | yes | `{ALLOW: n, MODIFY: n, DENY: n}` |
| `rule_histogram` | object | yes | `{rule_id: count}` |
| `orders_submitted` | integer | yes | Orders sent to broker |
| `orders_filled` | integer | yes | Fills received |
| `final_equity` | number | yes | Portfolio equity at end |
| `final_positions` | object | yes | `{symbol: qty}` |
| `kill_switch_active` | boolean | yes | Kill switch state at end |
| `run_id` | string (UUID) | no | Run identifier |

Schema: [`run_summary.schema.json`](schemas/run_summary.schema.json)

---

## Correlation

The primary join key between audit and exec logs is **`intent_id`**. Every
audit event contains `intent.intent_id` and every exec event contains
`intent_id` at the top level.

When both logs are produced by `run_stream()`, they also share a **`run_id`**
UUID. Use `tools/correlate.py` to join the two logs into a per-intent timeline.
