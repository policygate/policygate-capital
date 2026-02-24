from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["enforce", "monitor"]
DecisionDefault = Literal["deny", "allow"]


class ExposureLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_position_pct: float = Field(..., gt=0.0, le=1.0)
    max_gross_exposure_x: float = Field(..., gt=0.0)
    max_net_exposure_x: Optional[float] = Field(default=None, gt=0.0)


class LossLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_loss_limit_pct: float = Field(..., gt=0.0, le=1.0)
    max_drawdown_pct: float = Field(..., gt=0.0, le=1.0)


class ExecutionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_orders_per_minute_global: int = Field(..., ge=1, le=10_000)
    max_orders_per_minute_by_strategy: int = Field(..., ge=1, le=10_000)


class KillSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_on_rules: List[str] = Field(default_factory=list)
    trip_after_n_violations: int = Field(..., ge=1, le=10_000)
    violation_window_seconds: int = Field(..., ge=1, le=365 * 24 * 3600)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = Field(default="enforce")
    decision: DecisionDefault = Field(default="deny")


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exposure: ExposureLimits
    loss: LossLimits
    execution: ExecutionLimits
    kill_switch: KillSwitch


class SymbolOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exposure: Optional[ExposureLimits] = None
    loss: Optional[LossLimits] = None
    execution: Optional[ExecutionLimits] = None


class StrategyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exposure: Optional[ExposureLimits] = None
    loss: Optional[LossLimits] = None
    execution: Optional[ExecutionLimits] = None


class Overrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: Dict[str, SymbolOverride] = Field(default_factory=dict)
    strategies: Dict[str, StrategyOverride] = Field(default_factory=dict)


class CapitalPolicy(BaseModel):
    """Policy DSL v0.1 — strict, deterministic, fail-closed."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["0.1"] = Field(default="0.1")
    timezone: str = Field(default="UTC")
    defaults: Defaults = Field(default_factory=Defaults)
    limits: Limits
    overrides: Overrides = Field(default_factory=Overrides)

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_utc(cls, v: str) -> str:
        if v.upper() != "UTC":
            raise ValueError("v0.1 requires timezone: UTC")
        return "UTC"

    def resolve_exposure(
        self, symbol: str, strategy_id: str
    ) -> ExposureLimits:
        """Return effective exposure limits after applying overrides.

        Precedence: symbol > strategy > defaults.
        """
        base = self.limits.exposure
        sym = self.overrides.symbols.get(symbol)
        strat = self.overrides.strategies.get(strategy_id)
        if sym and sym.exposure:
            return sym.exposure
        if strat and strat.exposure:
            return strat.exposure
        return base

    def resolve_execution(
        self, strategy_id: str
    ) -> ExecutionLimits:
        """Return effective execution limits after applying overrides."""
        base = self.limits.execution
        strat = self.overrides.strategies.get(strategy_id)
        if strat and strat.execution:
            return strat.execution
        return base
