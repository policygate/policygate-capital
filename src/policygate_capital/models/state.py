from __future__ import annotations

from typing import Dict, List, Tuple

from pydantic import BaseModel, ConfigDict, Field


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str  # RFC 3339 UTC
    prices: Dict[str, float]


class PortfolioState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equity: float = Field(..., gt=0.0)
    start_of_day_equity: float = Field(..., gt=0.0)
    peak_equity: float = Field(..., gt=0.0)
    positions: Dict[str, float] = Field(default_factory=dict)
    realized_pnl_today: float = Field(default=0.0)
    unrealized_pnl: float = Field(default=0.0)


class ExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders_last_60s_global: int = Field(default=0, ge=0)
    orders_last_60s_by_strategy: Dict[str, int] = Field(default_factory=dict)
    violations_last_window: List[Tuple[str, str]] = Field(
        default_factory=list
    )  # list of (timestamp, rule_id)
    kill_switch_active: bool = Field(default=False)
