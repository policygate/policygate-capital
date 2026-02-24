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

33 tests passing across 8 test modules:
- Schema validation (8) — valid parsing, invalid rejection, extra field rejection, timezone enforcement
- Determinism (2) — identical outputs across repeated evaluations
- Exposure rules (5) — small trade allow, position MODIFY, gross deny, net deny, symbol override
- Loss rules (4) — daily loss deny, drawdown deny + kill switch trip, boundary check, no-loss allow
- Execution throttles (4) — global deny, strategy deny, under-limit allow, strategy override
- Kill switch (3) — active deny, inactive allow, checked-before-other-rules
- Fail-closed (3) — missing price, zero price, negative price
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

### Next Steps
- [ ] CLI interface (`policygate-eval --policy ... --intent ... --state ...`)
- [ ] Monitor mode (log violations but return ALLOW)
- [ ] Golden test fixtures (canonical JSON outputs for regression)
- [ ] CI workflow (GitHub Actions)
- [ ] PyPI publish

### Future
- [ ] Broker adapter interface (paper trading integration)
- [ ] Real-time execution state tracking (sliding window counters)
- [ ] Policy hot-reload without engine restart
- [ ] Multi-strategy portfolio aggregation
- [ ] Dashboard / audit log viewer
