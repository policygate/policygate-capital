from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    asset_class: Literal["equity", "crypto", "fx", "futures"]


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    timestamp: str  # RFC 3339 UTC
    strategy_id: str
    account_id: str
    instrument: Instrument
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    qty: float = Field(..., gt=0.0)
    limit_price: Optional[float] = Field(default=None, ge=0.0)
