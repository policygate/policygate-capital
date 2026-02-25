# PolicyGate Capital — Milestones

## v0.1.0 — Capital Policy Engine (2026-02-11)

**Status: COMPLETE**

Initial implementation of the deterministic Capital Policy Engine (CPE) — the core runtime governance layer for autonomous capital systems.

### What Was Built

- **Strict YAML DSL** for capital constraints (Pydantic-validated, `extra="forbid"`, UTC-only)
- **Deterministic evaluation pipeline** with fixed rule ordering:
  1. Kill switch (KILL-001)
  2. Loss limits — daily loss (LOSS-001), drawdown (LOSS-002)
  3. Execution throttles — global (EXEC-001), per-strategy (EXEC-002)
  4. Exposure checks — position cap (EXP-001), gross (EXP-002), net (EXP-003)
- **Three decision types**: ALLOW, DENY, MODIFY (position cap reduction)
- **Fail-closed behavior**: missing/invalid market data triggers SYS-001 deny
- **Override resolution**: symbol > strategy > defaults precedence
- **Kill switch auto-trip**: LOSS-002 (drawdown breach) triggers kill switch when configured
- **Append-only JSONL audit trail** with SHA-256 policy hash
- **Replay module** for deterministic verification of recorded decisions

### Rule Set

| Rule ID  | Domain    | Severity | Trigger |
|----------|-----------|----------|---------|
| KILL-001 | Kill switch | CRIT   | Kill switch active |
| LOSS-001 | Loss      | HIGH     | Daily return breaches limit |
| LOSS-002 | Loss      | CRIT     | Drawdown breaches limit (trips kill switch) |
| EXEC-001 | Execution | HIGH     | Global order rate exceeded |
| EXEC-002 | Execution | HIGH     | Per-strategy order rate exceeded |
| EXP-001  | Exposure  | HIGH     | Position size breaches limit (MODIFY if possible) |
| EXP-002  | Exposure  | HIGH     | Gross exposure breaches limit |
| EXP-003  | Exposure  | HIGH     | Net exposure breaches limit |
| SYS-001  | System    | CRIT     | Missing/invalid price data (fail-closed) |

### Test Coverage

39 tests passing at release across 9 test modules:
- Schema validation (8) — valid parsing, invalid rejection, extra field rejection, timezone enforcement
- Determinism (2) — identical outputs across repeated evaluations
- Exposure rules (5) — small trade allow, position MODIFY, gross deny, net deny, symbol override
- Loss rules (4) — daily loss deny, drawdown deny + kill switch trip, boundary check, no-loss allow
- Execution throttles (4) — global deny, strategy deny, under-limit allow, strategy override
- Kill switch (3) — active deny, inactive allow, all violations collected for audit
- Fail-closed (3) — missing price, zero price, negative price
- Monitor mode (6) — allows despite violations, still denies on missing price, clean allow
- Audit + replay (4) — event roundtrip, replay allow, replay deny, append multiple

### Architecture

```
src/policygate_capital/
├── engine/
│   ├── policy_engine.py    # Top-level PolicyEngine class
│   ├── evaluator.py        # Derived metrics + evaluation pipeline
│   ├── rules.py            # Pure rule functions (no side effects)
│   ├── decisions.py        # Decision, Violation, Evidence models
│   ├── audit.py            # JSONL audit emitter
│   └── replay.py           # Audit replay + comparison
├── models/
│   ├── policy.py           # CapitalPolicy YAML DSL (Pydantic)
│   ├── intent.py           # OrderIntent model
│   └── state.py            # PortfolioState, MarketSnapshot, ExecutionState
└── util/
    ├── io.py               # Strict YAML/JSON loading
    ├── hashing.py          # SHA-256 policy hash
    └── errors.py           # Typed exceptions
```

### Context

PolicyGate pivoted from general AI agent governance to **runtime governance for autonomous capital systems**. The CPE is the core product — a deterministic enforcement layer that sits between signal generation and execution, enforcing position limits, exposure caps, loss limits, drawdown thresholds, execution throttles, and kill switches.

ClawShield (the original PolicyGate product) remains as the static config validation / pre-deployment posture checker for AI agent deployments.

---

## Roadmap

### Completed Post-Release (2026-02-11)
- [x] CI workflow — GitHub Actions across Python 3.10–3.13
- [x] Monitor mode — log violations but always ALLOW (SYS-001 still denies)
- [x] CLI — `policygate-eval` with `--pretty` and `--audit-log` flags
- [x] README — install, usage (library + CLI), rule reference, audit docs
- [x] PyPI publish — https://pypi.org/project/policygate-capital/0.1.0/

### Completed Post-Release (2026-02-25)
- [x] Golden test fixtures (canonical JSON outputs for regression)
- [x] Broker adapter interface + SimBrokerAdapter + AlpacaBrokerAdapter
- [x] Stream runner (`policygate-run`) with state evolution, kill switch auto-trip, audit log
- [x] Runner tests (8) — determinism, counts, positions, kill switch hard/soft trip, audit, limit orders

### Completed (2026-02-25) — Tradier Broker + Runner Enhancements
- [x] **Broker injection** — `run_stream()` accepts `broker: Optional[BrokerAdapter]` (default SimBrokerAdapter)
- [x] **Execution event log** — separate from governance audit; emits ORDER_SUBMITTED, ORDER_FILLED, ORDER_REJECTED to JSONL
- [x] **TradierBrokerAdapter** — Tradier REST API (sandbox + live) with:
  - `_request()` thin HTTP layer for testability
  - `requests.Session` with 10s timeout, 2 retries on 429/5xx
  - `submit()` with `tag=intent_id` for traceability
  - `cancel()` via DELETE
  - `poll_fills()` — account-level primary, per-order fallback
  - `get_order()` with full status mapping
  - Strict `TRADIER_ENV` validation (sandbox|live)
- [x] **CLI enhancements** — `--broker sim|alpaca|tradier`, `--exec-log` path, lazy imports with actionable error messages
- [x] **Paper equities flow demo** — 7 intents, `--broker` arg, split replay (decision vs broker), outputs audit + exec + summary
- [x] **`pyproject.toml`** — `tradier = ["requests>=2.28.0,<3.0"]` optional dependency
- [x] **Tradier tests** (12) — submit market/limit, cancel, poll_fills (account + fallback), status mapping, credentials, env validation, tag traceability, empty orders, gated integration test

### Completed (2026-02-25) — Contracts, Correlation, Chaos Tests, Reference Deploy
- [x] **Event correlation IDs** — `run_id` (UUID per `run_stream()` invocation) threaded into all audit events, exec events, and run summary; `policy_hash` added to every exec event
- [x] **Fail-loud + ORDER_REJECTED on broker exception** — `broker.submit()` wrapped in try/except; emits ORDER_REJECTED exec event for observability, then re-raises. v0.1 failure contract pinned by chaos tests.
- [x] **JSON Schema contracts** — 11 hand-written JSON Schema files in `docs/schemas/` with `$ref` linking (audit_event, execution_event, run_summary, decision, violation, evidence, order_intent, instrument, portfolio_state, market_snapshot, execution_state)
- [x] **`docs/contracts.md`** — stable schema documentation with semver stability policy (fields append-only within major version)
- [x] **Schema validation tests** (6) — validates both single-intent and 7-intent paper flow against schemas; `jsonschema>=4.0` as non-optional dev dep
- [x] **`tools/correlate.py`** — standalone CLI joining audit + exec JSONL by `intent_id` into per-intent timeline; supports `--out timeline.jsonl` for persistent artifact
- [x] **Runner chaos tests** (3) — `ThrottlingBroker` (ConnectionError), `TimeoutBroker` (TimeoutError); verifies audit survives, ORDER_REJECTED emitted, exception propagates (fail-loud baseline)
- [x] **Reference deployment** — `deploy/compose/` with Dockerfile (`python:3.12-slim`), `docker-compose.yml` (bind-mount inputs, named volume for outputs, env var injection for Tradier), README
- [x] **Golden test updated** — `_normalize_event()` masks `run_id`; fixture regenerated

### Test Coverage (current)

82 tests across 16 test modules (81 passing, 1 skipped integration):
- Schema validation (8)
- Determinism (2)
- Exposure rules (5)
- Loss rules (4)
- Execution throttles (4)
- Kill switch (3)
- Fail-closed (3)
- Monitor mode (6)
- Audit + replay (4)
- Runner (10) — stream eval, state evolution, kill switch hard/soft trip, audit, limit orders, run_id in audit events, run_id in exec events
- Runner chaos (3) — throttle, timeout, fail-loud propagation
- Event schemas (6) — single-intent + paper flow validated against JSON Schemas
- Alpaca broker (9) — mocked SDK, market/limit orders, cancel, fills, status mapping, equity, positions, credentials
- Tradier broker (12) — mocked `_request()`, market/limit orders, cancel, fills (account + fallback), status mapping, credentials, env, tag, empty orders
- Demo golden (2) — audit match, semantics smoke

### Next Steps
- [ ] HTTP intake surface (`policygate-serve` with `POST /intent`)
- [ ] Quickstart Docker Compose stack (serve + intent producer)
- [ ] Eval latency metric (`eval_ms` in audit events + `tools/stats.py`)
- [ ] Scenario packs (normal day, throttle burst, drawdown crash) + architecture diagram
- [ ] Policy hot-reload without engine restart
- [ ] Multi-strategy portfolio aggregation
- [ ] Dashboard / audit log viewer
