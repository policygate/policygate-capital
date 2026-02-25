"""Top-level PolicyEngine: load policy, evaluate intents, emit audit events."""

from __future__ import annotations

import time
from pathlib import Path

from policygate_capital.engine.decisions import Decision
from policygate_capital.engine.evaluator import evaluate
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.policy import CapitalPolicy
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.util.hashing import policy_hash
from policygate_capital.util.io import load_policy_yaml


class PolicyEngine:
    """Deterministic capital policy evaluation engine."""

    def __init__(self, policy_path: str | Path) -> None:
        self._policy_path = Path(policy_path)
        self._policy_raw = self._policy_path.read_text(encoding="utf-8")
        self._policy: CapitalPolicy = load_policy_yaml(self._policy_path)
        self._policy_hash: str = policy_hash(self._policy_raw)

    @property
    def policy(self) -> CapitalPolicy:
        return self._policy

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    def evaluate(
        self,
        intent: OrderIntent,
        portfolio: PortfolioState,
        market: MarketSnapshot,
        execution: ExecutionState,
    ) -> Decision:
        """Evaluate an order intent against the loaded policy."""
        t0 = time.perf_counter_ns()
        decision = evaluate(
            intent=intent,
            policy=self._policy,
            portfolio=portfolio,
            market=market,
            execution=execution,
        )
        t1 = time.perf_counter_ns()
        decision.eval_ms = round((t1 - t0) / 1_000_000, 3)
        return decision
