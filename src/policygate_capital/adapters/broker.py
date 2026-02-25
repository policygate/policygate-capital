"""Broker adapter protocol and core data types.

Defines the minimal interface that any broker (sim or live) must implement.
No authentication, no retries, no broker-specific fields.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import MarketSnapshot

OrderStatus = Literal["pending", "filled", "cancelled", "rejected"]


class BrokerOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    order_type: Literal["market", "limit"]
    limit_price: Optional[float] = None
    status: OrderStatus = "pending"


class Fill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    price: float
    timestamp: str


@runtime_checkable
class BrokerAdapter(Protocol):
    def submit(
        self, intent: OrderIntent, market: MarketSnapshot
    ) -> str:
        """Submit an order intent. Returns a broker order ID."""
        ...

    def cancel(self, order_id: str) -> None:
        """Cancel a pending order."""
        ...

    def poll_fills(
        self, since_ts: str | None = None
    ) -> List[Fill]:
        """Return fills since the given timestamp (or all if None)."""
        ...
