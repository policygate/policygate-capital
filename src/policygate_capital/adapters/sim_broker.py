"""Deterministic simulated broker.

Rules:
  - Market orders fill immediately at mid price.
  - Limit BUY fills if limit_price >= mid_price (at mid).
  - Limit SELL fills if limit_price <= mid_price (at mid).
  - No partial fills, no slippage, no fees.
  - All behavior is deterministic given intent + market snapshot.
"""

from __future__ import annotations

from typing import List, Optional

from policygate_capital.adapters.broker import BrokerOrder, Fill
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import MarketSnapshot


class SimBrokerAdapter:
    """Deterministic paper broker for testing and demos."""

    def __init__(self) -> None:
        self._orders: dict[str, BrokerOrder] = {}
        self._fills: list[Fill] = []
        self._next_id: int = 1

    def submit(
        self, intent: OrderIntent, market: MarketSnapshot
    ) -> str:
        symbol = intent.instrument.symbol
        mid_price = market.prices.get(symbol)

        order_id = f"SIM-{self._next_id:06d}"
        self._next_id += 1

        order = BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
        )

        if mid_price is None or mid_price <= 0:
            order.status = "rejected"
            self._orders[order_id] = order
            return order_id

        # Determine if the order fills
        fills = False
        if intent.order_type == "market":
            fills = True
        elif intent.order_type == "limit" and intent.limit_price is not None:
            if intent.side == "buy" and intent.limit_price >= mid_price:
                fills = True
            elif intent.side == "sell" and intent.limit_price <= mid_price:
                fills = True

        if fills:
            order.status = "filled"
            self._orders[order_id] = order
            self._fills.append(
                Fill(
                    order_id=order_id,
                    symbol=symbol,
                    side=intent.side,
                    qty=intent.qty,
                    price=mid_price,
                    timestamp=intent.timestamp,
                )
            )
        else:
            order.status = "rejected"
            self._orders[order_id] = order

        return order_id

    def cancel(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order and order.status == "pending":
            order.status = "cancelled"

    def poll_fills(
        self, since_ts: str | None = None
    ) -> List[Fill]:
        if since_ts is None:
            return list(self._fills)
        return [f for f in self._fills if f.timestamp >= since_ts]

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        return self._orders.get(order_id)
