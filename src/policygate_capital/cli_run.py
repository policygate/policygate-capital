"""CLI entry point: policygate-run.

Runs a stream of order intents through the Capital Policy Engine
with a simulated broker, producing an audit log and summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.runtime.runner import run_stream
from policygate_capital.util.io import load_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="policygate-run",
        description="Run an order stream through the CPE with a sim broker.",
    )
    parser.add_argument(
        "--policy", required=True, help="Path to policy YAML file."
    )
    parser.add_argument(
        "--intents", required=True, help="Path to JSONL file of OrderIntents."
    )
    parser.add_argument(
        "--portfolio", required=True, help="Path to initial portfolio state JSON."
    )
    parser.add_argument(
        "--market", required=True, help="Path to market snapshot JSON."
    )
    parser.add_argument(
        "--execution", default=None, help="Path to initial execution state JSON."
    )
    parser.add_argument(
        "--audit-log", default=None, help="Path to JSONL audit log output."
    )
    parser.add_argument(
        "--out-summary", default=None, help="Path to write run summary JSON."
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output."
    )

    args = parser.parse_args(argv)

    try:
        # Load intents from JSONL
        intents_path = Path(args.intents)
        intents = []
        for line in intents_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                intents.append(OrderIntent.model_validate(json.loads(line)))

        portfolio = PortfolioState.model_validate(load_json(args.portfolio))
        market = MarketSnapshot.model_validate(load_json(args.market))

        if args.execution:
            execution = ExecutionState.model_validate(load_json(args.execution))
        else:
            execution = ExecutionState()

        # Clean audit log if it exists
        if args.audit_log:
            audit_path = Path(args.audit_log)
            if audit_path.exists():
                audit_path.unlink()
        else:
            audit_path = None

        summary, final_portfolio, final_execution = run_stream(
            policy_path=args.policy,
            intents=intents,
            portfolio=portfolio,
            execution=execution,
            market=market,
            audit_log_path=audit_path,
        )

        summary_dict = summary.to_dict(final_portfolio, final_execution)
        indent = 2 if args.pretty else None
        summary_json = json.dumps(summary_dict, indent=indent, sort_keys=True)

        print(summary_json)

        if args.out_summary:
            Path(args.out_summary).write_text(
                summary_json + "\n", encoding="utf-8"
            )

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
