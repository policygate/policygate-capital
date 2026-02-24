from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from policygate_capital.models.intent import OrderIntent

DecisionVerdict = Literal["ALLOW", "DENY", "MODIFY"]
Severity = Literal["LOW", "MED", "HIGH", "CRIT"]


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: Severity
    message: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    computed: Dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    value: Any
    limit: Any


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionVerdict
    intent_id: str
    modified_intent: Optional[OrderIntent] = None
    violations: List[Violation] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    kill_switch_triggered: bool = Field(default=False)
