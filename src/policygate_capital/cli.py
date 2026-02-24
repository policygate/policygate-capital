"""CLI entry point: policygate-eval."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from policygate_capital.engine.audit import build_audit_event, write_audit_event
from policygate_capital.engine.policy_engine import PolicyEngine
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.util.io import load_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="policygate-eval",
        description="Evaluate an order intent against a capital policy.",
    )
    parser.add_argument(
        "--policy", required=True, help="Path to policy YAML file."
    )
    parser.add_argument(
        "--intent", required=True, help="Path to order intent JSON file."
    )
    parser.add_argument(
        "--portfolio", required=True, help="Path to portfolio state JSON file."
    )
    parser.add_argument(
        "--market", required=True, help="Path to market snapshot JSON file."
    )
    parser.add_argument(
        "--execution",
        default=None,
        help="Path to execution state JSON file. Defaults to empty state.",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Path to JSONL audit log. If set, appends an audit event.",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output."
    )

    args = parser.parse_args(argv)

    try:
        engine = PolicyEngine(args.policy)
        intent = OrderIntent.model_validate(load_json(args.intent))
        portfolio = PortfolioState.model_validate(load_json(args.portfolio))
        market = MarketSnapshot.model_validate(load_json(args.market))

        if args.execution:
            execution = ExecutionState.model_validate(load_json(args.execution))
        else:
            execution = ExecutionState()

        decision = engine.evaluate(intent, portfolio, market, execution)

        output = decision.model_dump(mode="json")
        indent = 2 if args.pretty else None
        print(json.dumps(output, indent=indent, sort_keys=True))

        if args.audit_log:
            event = build_audit_event(
                decision=decision,
                intent=intent,
                portfolio=portfolio,
                market=market,
                execution=execution,
                policy_hash=engine.policy_hash,
            )
            write_audit_event(args.audit_log, event)

        # Exit code: 0 for ALLOW/MODIFY, 1 for DENY
        return 0 if decision.decision in ("ALLOW", "MODIFY") else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
