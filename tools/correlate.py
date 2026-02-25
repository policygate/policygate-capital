#!/usr/bin/env python3
"""Correlate audit + exec logs into a per-intent timeline.

Joins governance audit events and execution events by intent_id,
producing a human-readable timeline showing exactly why each intent
was decided and what happened during execution.

Usage:
    python tools/correlate.py --audit audit.jsonl --exec exec.jsonl
    python tools/correlate.py --audit audit.jsonl --exec exec.jsonl --intent paper-001
    python tools/correlate.py --audit audit.jsonl --exec exec.jsonl --out timeline.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def correlate(
    audit_events: list[dict],
    exec_events: list[dict],
    intent_filter: str | None = None,
) -> dict[str, list[dict]]:
    """Build a timeline grouped by intent_id.

    Returns {intent_id: [timeline_entry, ...]} sorted by timestamp.
    Each timeline_entry is a dict with: ts, source, event, details.
    """
    timeline: dict[str, list[dict]] = defaultdict(list)

    for ae in audit_events:
        iid = ae.get("intent", {}).get("intent_id", "?")
        if intent_filter and iid != intent_filter:
            continue
        d = ae.get("decision", {})
        entry = {
            "ts": ae.get("timestamp", ""),
            "source": "audit",
            "event": f"DECISION_{d.get('decision', '?')}",
            "intent_id": iid,
            "violations": len(d.get("violations", [])),
            "kill_switch_triggered": d.get("kill_switch_triggered", False),
        }
        run_id = ae.get("run_id")
        if run_id:
            entry["run_id"] = run_id
        policy_hash = ae.get("policy_hash")
        if policy_hash:
            entry["policy_hash"] = policy_hash
        timeline[iid].append(entry)

    for ee in exec_events:
        iid = ee.get("intent_id", "?")
        if intent_filter and iid != intent_filter:
            continue
        entry = {
            "ts": ee.get("ts", ""),
            "source": "exec",
            "event": ee.get("event", "?"),
            "intent_id": iid,
            "order_id": ee.get("order_id", ""),
        }
        for field in ("symbol", "side", "qty", "price", "order_type"):
            if field in ee:
                entry[field] = ee[field]
        run_id = ee.get("run_id")
        if run_id:
            entry["run_id"] = run_id
        timeline[iid].append(entry)

    # Sort each intent's entries by timestamp
    for iid in timeline:
        timeline[iid].sort(key=lambda x: x.get("ts", ""))

    return dict(sorted(timeline.items()))


def _print_timeline(timeline: dict[str, list[dict]]) -> None:
    """Print a human-readable timeline to stdout."""
    for iid, entries in timeline.items():
        print(f"\nintent_id: {iid}")
        for e in entries:
            ts = e.get("ts", "?")
            src = e["source"]
            event = e["event"]

            if src == "audit":
                violations = e.get("violations", 0)
                ks = " KILL_SWITCH" if e.get("kill_switch_triggered") else ""
                print(f"  [{ts}] {event}  violations={violations}{ks}")
            else:
                parts = [event]
                if e.get("order_id"):
                    parts.append(f"order_id={e['order_id']}")
                for f in ("symbol", "side", "qty", "price"):
                    if f in e:
                        parts.append(f"{f}={e[f]}")
                print(f"  [{ts}] {'  '.join(parts)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Join audit + exec logs into a per-intent timeline.",
    )
    parser.add_argument("--audit", required=True, help="Path to audit JSONL.")
    parser.add_argument("--exec", required=True, help="Path to exec JSONL.")
    parser.add_argument("--intent", default=None, help="Filter to a single intent_id.")
    parser.add_argument("--out", default=None, help="Write timeline as JSONL to this path.")
    args = parser.parse_args(argv)

    audit_events = _load_jsonl(Path(args.audit))
    exec_events = _load_jsonl(Path(args.exec))

    timeline = correlate(audit_events, exec_events, intent_filter=args.intent)

    _print_timeline(timeline)

    if args.out:
        out_path = Path(args.out)
        with open(out_path, "w", encoding="utf-8") as f:
            for iid, entries in timeline.items():
                for entry in entries:
                    f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        print(f"\nWritten to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
