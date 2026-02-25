# PolicyGate Capital — Architecture

## System Diagram

```
                         ┌─────────────────────────────────────────────┐
                         │         PolicyGate Capital (CPE)            │
                         │                                             │
  Signal Generator       │  ┌───────────────┐    ┌─────────────────┐  │
  ─────────────────┐     │  │ PolicyEngine   │    │ BrokerAdapter   │  │
                   │     │  │  .evaluate()   │    │  .submit()      │  │     Exchange /
  POST /intent ────┼────▶│  │               │    │  .poll_fills()  │──┼───▶ Broker API
  (HTTP)           │     │  │  Kill switch   │    │  .cancel()      │  │
                   │     │  │  Loss limits   │    │                 │  │
  JSONL file ──────┘     │  │  Exec throttle │    │  SimBroker      │  │
  (policygate-run)       │  │  Exposure caps │    │  AlpacaBroker   │  │
                         │  │               │    │  TradierBroker  │  │
                         │  └───────┬───────┘    └────────┬────────┘  │
                         │          │                     │           │
                         │          │   Decision          │  Fill     │
                         │          ▼                     ▼           │
                         │  ┌───────────────┐    ┌─────────────────┐  │
                         │  │ Audit Emitter  │    │ Exec Emitter    │  │
                         │  │ (pre-submit)   │    │ (post-submit)   │  │
                         │  └───────┬───────┘    └────────┬────────┘  │
                         │          │                     │           │
                         └──────────┼─────────────────────┼───────────┘
                                    │                     │
                                    ▼                     ▼
                            ┌──────────────┐     ┌──────────────┐
                            │ audit.jsonl   │     │ exec.jsonl    │
                            │ (governance)  │     │ (execution)   │
                            └──────────────┘     └──────────────┘
```

## Core Components

### Models (`src/policygate_capital/models/`)

| Module | Purpose |
|--------|---------|
| `policy.py` | YAML policy DSL — Pydantic models with `extra="forbid"` |
| `intent.py` | `OrderIntent` — the unit of evaluation |
| `state.py` | `PortfolioState`, `MarketSnapshot`, `ExecutionState` |

All models use strict Pydantic validation. Unknown fields are rejected (`extra="forbid"`). Timestamps are RFC 3339 UTC strings.

### Engine (`src/policygate_capital/engine/`)

| Module | Purpose |
|--------|---------|
| `rules.py` | Pure rule functions — each returns `Violation` or `None` |
| `evaluator.py` | Deterministic evaluation pipeline |
| `policy_engine.py` | Top-level `PolicyEngine` — loads policy, computes hash, times evaluation (`eval_ms`) |
| `decisions.py` | `Decision`, `Violation`, `Evidence` types |
| `audit.py` | Append-only JSONL audit emitter |
| `replay.py` | Replay verification — re-evaluate from audit log |

### Adapters (`src/policygate_capital/adapters/`)

| Module | Purpose |
|--------|---------|
| `broker.py` | `BrokerAdapter` protocol — `submit`, `cancel`, `poll_fills` |
| `sim_broker.py` | Deterministic simulated broker for testing |
| `alpaca_broker.py` | Alpaca Markets API adapter |
| `tradier_broker.py` | Tradier REST API adapter (sandbox + live) |

### Runtime (`src/policygate_capital/runtime/`)

| Module | Purpose |
|--------|---------|
| `runner.py` | Stream runner — loops intents through engine + broker, evolves state |

### CLI Entry Points

| Command | Module | Purpose |
|---------|--------|---------|
| `policygate-eval` | `cli.py` | Evaluate a single intent |
| `policygate-run` | `cli_run.py` | Run an intent stream with configurable broker |
| `policygate-serve` | `cli_serve.py` | HTTP server for real-time intent evaluation |

## Evaluation Pipeline

The evaluator runs rules in a **fixed order** on every intent. All rules are evaluated (no short-circuiting) to provide a complete violation picture for audit.

```
1. SYS-001  — fail-closed (missing price → immediate DENY, even in monitor mode)
2. KILL-001 — kill switch check
3. LOSS-001 — daily loss limit
4. LOSS-002 — max drawdown (hard-trips kill switch)
5. EXEC-001 — global order rate
6. EXEC-002 — per-strategy order rate
7. EXP-001  — per-symbol position cap (supports MODIFY)
8. EXP-002  — gross exposure
9. EXP-003  — net exposure
```

After all rules execute, the verdict is determined:

- **No violations** → `ALLOW`
- **Only EXP-001 with `allowed_qty > 0`** → `MODIFY` (reduce qty to fit within cap)
- **Any other violations** → `DENY`

In **monitor mode**, the verdict is always `ALLOW` (except SYS-001), but all violations are recorded.

## Integration Modes

| Mode | Entry Point | State Management | Use Case |
|------|-------------|------------------|----------|
| **CLI batch** | `policygate-run` | In-process, single run | Backtesting, batch evaluation |
| **HTTP server** | `policygate-serve` | In-process, persistent | Real-time integration |
| **Library** | `run_stream()` / `PolicyEngine.evaluate()` | Caller-managed | Embedded in trading system |

### HTTP Intake (`policygate-serve`)

- `POST /intent` — evaluate an `OrderIntent`, return `Decision` JSON
- `GET /health` — server status (run_id, policy_hash, positions, kill switch)
- Hardened: 64KB body limit, Content-Type enforcement, optional Bearer token auth
- Thread-safe: single lock around evaluate + state mutation + broker submit
- Binds to `127.0.0.1` by default; use a reverse proxy for external exposure

## Data Flow

```
Intent ──▶ evaluate() ──▶ Decision
                │              │
                │ (audit)      │ (if ALLOW/MODIFY)
                ▼              ▼
          audit.jsonl    broker.submit()
                              │
                         ┌────┴────┐
                         │         │
                    poll_fills()  get_order()
                         │         │
                         ▼         ▼
                    exec.jsonl  exec.jsonl
                   (FILLED)    (REJECTED)
```

## State Evolution (Runtime Runner)

The stream runner (`run_stream`) processes intents sequentially and evolves state:

1. **Evaluate** intent against policy
2. **Audit** the decision (append to JSONL — always before submit)
3. **Submit** to broker if ALLOW/MODIFY
4. **Apply fills** — update portfolio positions
5. **Update execution counters** — order rate tracking
6. **Record violations** — append to rolling window
7. **Evict stale violations** — remove outside `violation_window_seconds`
8. **Check kill switch** — trip after N violations in window or on LOSS-002

## Override Resolution

Policy limits can be overridden per-symbol or per-strategy:

```
Precedence: symbol override > strategy override > defaults
```

Override blocks are optional — if not present, the default limits from `limits:` apply.

## Failure Model

### Fail-Closed (SYS-001)

Missing or invalid price data triggers an immediate DENY. The CPE never
evaluates exposure rules without a valid price. This applies in both
enforce and monitor mode.

### Fail-Loud (Broker Exception)

When `broker.submit()` raises an exception:

1. `ORDER_REJECTED` exec event is emitted (for observability)
2. The exception is re-raised (run halts)
3. Audit event was already written pre-submit (survives the failure)

This is the v0.1 failure contract. Future versions may add configurable
retry logic.

## Determinism Boundary

| Component | Deterministic? | Notes |
|-----------|---------------|-------|
| Policy evaluation | **Yes** | Same inputs = same Decision, always |
| Rule ordering | **Yes** | Fixed: KILL → LOSS → EXEC → EXP |
| Audit events | **Yes** | Deterministic content (timestamps vary) |
| Broker I/O | **No** | External system, network latency, partial fills |
| Fill prices | **No** | Market-dependent |

**Replay guarantee**: `policygate_capital.engine.replay` can verify that
recorded audit events produce identical decisions when re-evaluated.
This proves governance correctness. Broker outcomes (fills, rejections)
are non-deterministic and cannot be replayed.

## Contracts

- **Schema definitions**: [`docs/schemas/`](schemas/) — 11 JSON Schema
  files with `$ref` linking
- **Stability policy**: [`docs/contracts.md`](contracts.md) — fields are
  append-only within a major version; no removals or type changes
- **Event correlation**: `intent_id` joins audit and exec events;
  `run_id` (UUID) groups events from a single run; `policy_hash`
  (SHA-256) pins the policy version

## Operational Tools

| Tool | Purpose |
|------|---------|
| `tools/correlate.py` | Join audit + exec logs by `intent_id` into per-intent timeline |
| `tools/stats.py` | Compute p50/p95/p99 eval latency from audit JSONL |
| `scenarios/*/run.py` | Pre-built scenario packs (normal day, throttle burst, drawdown crash) |

## Dependencies

- `pydantic>=2.0,<3.0` — model validation
- `PyYAML>=6.0,<7.0` — policy YAML parsing
- No other runtime dependencies (broker adapters are optional extras)
