"""Golden tests for the end-to-end demo.

Runs demos/cpe_demo.py, normalizes the produced audit JSONL (event_id +
timestamp), and compares byte-for-byte against the golden fixture.

To update the golden file after an intentional change:
  1. Run: python demos/cpe_demo.py
  2. Run the normalization snippet in this file's docstring (or just copy
     the failing test's "produced" output to the golden path).
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "demo_audit.normalized.jsonl"


def _run_demo() -> None:
    demo_script = REPO_ROOT / "demos" / "cpe_demo.py"
    assert demo_script.exists(), f"Demo script not found at: {demo_script}"

    proc = subprocess.run(
        [sys.executable, str(demo_script)],
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"Demo script failed (rc={proc.returncode}).\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


def _find_audit_log() -> Path:
    candidates = [
        REPO_ROOT / "demos" / "out" / "demo_audit.jsonl",
        REPO_ROOT / "demos" / "demo_audit.jsonl",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise AssertionError(
        "Could not locate demo audit log. Looked in:\n"
        + "\n".join(f"  - {p}" for p in candidates)
    )


def _read_jsonl(path: Path) -> List[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _normalize_event(evt: dict) -> dict:
    out = dict(evt)
    if "event_id" in out:
        out["event_id"] = "<EVENT_ID>"
    if "timestamp" in out:
        out["timestamp"] = "<EVENT_TS>"
    if "run_id" in out:
        out["run_id"] = "<RUN_ID>"
    if "eval_ms" in out:
        out["eval_ms"] = "<EVAL_MS>"
    if "decision" in out and isinstance(out["decision"], dict):
        if "eval_ms" in out["decision"]:
            out["decision"] = dict(out["decision"])
            out["decision"]["eval_ms"] = "<EVAL_MS>"
    return out


def _canonical_jsonl(events: Iterable[dict]) -> str:
    lines = []
    for evt in events:
        lines.append(
            json.dumps(evt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
    return "\n".join(lines) + "\n"


def test_demo_audit_matches_golden() -> None:
    """Byte-for-byte comparison of normalized demo output against golden fixture."""
    _run_demo()
    produced_path = _find_audit_log()

    produced_events = _read_jsonl(produced_path)
    normalized = [_normalize_event(e) for e in produced_events]
    produced_canon = _canonical_jsonl(normalized)

    assert GOLDEN_PATH.exists(), (
        f"Golden file not found at {GOLDEN_PATH}\n"
        "Generate it by running the demo and normalizing the output."
    )

    golden_text = GOLDEN_PATH.read_text(encoding="utf-8")

    if produced_canon != golden_text:
        diff = "".join(
            difflib.unified_diff(
                golden_text.splitlines(keepends=True),
                produced_canon.splitlines(keepends=True),
                fromfile=str(GOLDEN_PATH),
                tofile=f"{produced_path} (normalized)",
            )
        )
        raise AssertionError(
            "Demo audit output does not match golden fixture.\n\n"
            "If this change is intentional, overwrite the golden file:\n"
            f"  {GOLDEN_PATH}\n\n"
            f"Diff:\n{diff}"
        )


def test_demo_semantics_smoke() -> None:
    """Semantic assertions — easier to debug than a pure golden diff."""
    _run_demo()
    produced_path = _find_audit_log()
    events = _read_jsonl(produced_path)

    assert len(events) == 5, f"Expected 5 audit events, got {len(events)}"

    decisions = [e["decision"] for e in events]
    verdicts = [d["decision"] for d in decisions]
    assert verdicts == ["ALLOW", "MODIFY", "DENY", "DENY", "DENY"]

    # Step 2: MODIFY reduces qty to 40
    assert decisions[1]["modified_intent"]["qty"] == 40.0

    # Step 3: gross exposure violation
    step3_rules = {v["rule_id"] for v in decisions[2]["violations"]}
    assert "EXP-002" in step3_rules

    # Step 4: drawdown trips kill switch
    step4_rules = {v["rule_id"] for v in decisions[3]["violations"]}
    assert "LOSS-002" in step4_rules
    assert decisions[3]["kill_switch_triggered"] is True

    # Step 5: kill switch active
    step5_rules = {v["rule_id"] for v in decisions[4]["violations"]}
    assert "KILL-001" in step5_rules
