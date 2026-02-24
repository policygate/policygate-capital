"""Append-only JSONL audit emitter."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from policygate_capital.engine.decisions import Decision
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.version import __version__


def build_audit_event(
    decision: Decision,
    intent: OrderIntent,
    portfolio: PortfolioState,
    market: MarketSnapshot,
    execution: ExecutionState,
    policy_hash: str,
    engine_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a structured audit event dict (serialisable to JSON)."""
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "engine_version": engine_version or __version__,
        "policy_hash": policy_hash,
        "intent": intent.model_dump(mode="json"),
        "portfolio_state": portfolio.model_dump(mode="json"),
        "market_snapshot": market.model_dump(mode="json"),
        "execution_state": execution.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }


def write_audit_event(
    path: str | Path, event: Dict[str, Any]
) -> None:
    """Append a single audit event as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_audit_events(path: str | Path) -> list[Dict[str, Any]]:
    """Read all audit events from a JSONL file."""
    path = Path(path)
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
