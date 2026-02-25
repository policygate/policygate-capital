# Audit Trail & Replay

## Audit Trail

Every evaluation produces an audit event — a self-contained record of the intent, all input state, and the engine's decision. Events are written as append-only JSONL (one JSON object per line).

### Audit Event Schema

```json
{
  "event_id": "uuid-v4",
  "timestamp": "2026-02-24T00:00:01.123456+00:00",
  "engine_version": "0.1.0",
  "policy_hash": "sha256-of-policy-yaml",
  "intent": { /* full OrderIntent */ },
  "portfolio_state": { /* full PortfolioState at evaluation time */ },
  "market_snapshot": { /* full MarketSnapshot */ },
  "execution_state": { /* full ExecutionState */ },
  "decision": {
    "decision": "ALLOW | MODIFY | DENY",
    "intent_id": "...",
    "violations": [ /* list of Violation objects */ ],
    "evidence": [ /* list of Evidence objects */ ],
    "modified_intent": null,
    "kill_switch_triggered": false
  }
}
```

### Key Properties

- **Self-contained**: Each event includes all inputs needed to reproduce the decision.
- **Append-only**: Events are only appended, never modified or deleted.
- **Deterministic serialization**: `json.dumps(event, sort_keys=True, separators=(",", ":"))` ensures byte-stable output.
- **Policy hash**: SHA-256 of the policy YAML content, recorded in every event. Detect policy changes between events.

### Writing Audit Events

```python
from policygate_capital.engine.audit import build_audit_event, write_audit_event

event = build_audit_event(
    decision=decision,
    intent=intent,
    portfolio=portfolio,
    market=market,
    execution=execution,
    policy_hash=engine.policy_hash,
)
write_audit_event("audit.jsonl", event)
```

### Reading Audit Events

```python
from policygate_capital.engine.audit import read_audit_events

events = read_audit_events("audit.jsonl")
for event in events:
    print(event["decision"]["decision"], event["intent"]["intent_id"])
```

## Replay

Replay is the mechanism for verifying determinism. Given a recorded audit event, the engine reconstructs all inputs from the event and re-evaluates the intent. The replayed decision must match the original.

### How Replay Works

1. Extract `intent`, `portfolio_state`, `market_snapshot`, `execution_state` from the audit event.
2. Deserialize each into its Pydantic model.
3. Call `evaluate()` with these inputs and the same policy.
4. Compare the original `decision` from the event to the replayed result.

```python
from policygate_capital.engine.replay import replay_event, decisions_match
from policygate_capital.engine.policy_engine import PolicyEngine

engine = PolicyEngine("policy.yaml")

events = read_audit_events("audit.jsonl")
for event in events:
    original, replayed = replay_event(event, engine.policy)
    assert decisions_match(original, replayed), f"Mismatch: {event['event_id']}"
```

### What `decisions_match` Compares

- `decision` (ALLOW / MODIFY / DENY)
- `intent_id`
- `violations` (full list, including rule_id, severity, message, inputs, computed)
- `kill_switch_triggered`
- `modified_intent` (for MODIFY decisions)

### When Replay Fails

A replay mismatch means one of:

1. **Policy changed** — check `policy_hash` in the audit event vs. current policy hash.
2. **Engine bug** — the evaluation logic has a non-determinism or regression.
3. **Corrupted audit data** — the JSONL file was modified.

### CLI Replay

Using `policygate-eval`, you can replay individual events by providing the exact inputs from the audit trail:

```bash
policygate-eval \
  --policy policy.yaml \
  --intent intent.json \
  --portfolio portfolio.json \
  --market market.json \
  --execution execution.json \
  --pretty
```

### Golden Tests

The test suite includes golden tests (`tests/test_demo_golden.py`) that:

1. Run the 5-step demo scenario.
2. Normalize non-deterministic fields (`event_id` → `"<EVENT_ID>"`, `timestamp` → `"<EVENT_TS>"`).
3. Compare byte-for-byte against a golden fixture (`tests/fixtures/golden/demo_audit.normalized.jsonl`).
4. Also run semantic assertions on the decision sequence.

Updating the golden fixture after intentional changes:

```bash
python demos/cpe_demo.py  # regenerates demos/output/demo_audit.jsonl
# Then update the normalized golden file in tests/fixtures/golden/
```

## Evidence

Every decision includes an `evidence` list showing the computed metrics and their limits:

```json
{
  "metric": "daily_return",
  "value": -0.01,
  "limit": -0.02
}
```

Evidence fields:
- `daily_return` — `(equity - sod_equity) / sod_equity`
- `drawdown` — `(peak_equity - equity) / peak_equity`
- `new_position_pct` — proposed position value / equity
- `gross_exposure_x` — sum of |position values| / equity
- `net_exposure_x` — |sum of position values| / equity

Evidence is always present in the decision, even when no violations fire. This supports post-hoc analysis — you can examine how close a trade was to triggering a rule.
