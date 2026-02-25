"""Eval latency statistics from audit JSONL.

Usage:
    python tools/stats.py --audit audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def percentile(sorted_vals: list[float], pct: float) -> float:
    """Compute percentile from a sorted list (nearest-rank method)."""
    if not sorted_vals:
        return 0.0
    k = max(0, int(len(sorted_vals) * pct / 100) - 1)
    return sorted_vals[min(k, len(sorted_vals) - 1)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print eval latency stats from audit JSONL.",
    )
    parser.add_argument(
        "--audit", required=True, help="Path to audit JSONL file.",
    )
    args = parser.parse_args(argv)

    path = Path(args.audit)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1

    values: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        ms = event.get("eval_ms")
        if ms is not None:
            values.append(float(ms))

    if not values:
        print("No eval_ms values found in audit log.")
        return 0

    values.sort()
    total = len(values)
    mean = sum(values) / total

    print(f"Events:  {total}")
    print(f"Mean:    {mean:.3f} ms")
    print(f"p50:     {percentile(values, 50):.3f} ms")
    print(f"p95:     {percentile(values, 95):.3f} ms")
    print(f"p99:     {percentile(values, 99):.3f} ms")
    print(f"Max:     {values[-1]:.3f} ms")

    return 0


if __name__ == "__main__":
    sys.exit(main())
