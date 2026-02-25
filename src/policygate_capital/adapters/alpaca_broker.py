"""Alpaca paper trading broker adapter.

Wraps the alpaca-py SDK to implement the BrokerAdapter protocol.
Credentials are read from environment variables:
  - APCA_API_KEY_ID
  - APCA_API_SECRET_KEY

Install the optional dependency:
  pip install policygate-capital[alpaca]
"""

from __future__ import annotations

import os
from typing import List, Optional

from policygate_capital.adapters.broker import BrokerOrder, Fill
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import MarketSnapshot

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import (
        GetOrderByIdRequest,
        LimitOrderRequest,
        MarketOrderRequest,
    )
except ImportError as e:
    raise ImportError(
        "alpaca-py is required for the Alpaca adapter. "
        "Install with: pip install policygate-capital[alpaca]"
    ) from e


class AlpacaBrokerAdapter:
    """Paper trading adapter using the Alpaca API.

    Implements the BrokerAdapter protocol: submit, cancel, poll_fills.
    Uses paper=True by default (production use not recommended for v0.1).
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
    ) -> None:
        self._api_key = api_key or os.environ.get("APCA_API_KEY_ID", "")
        self._secret_key = secret_key or os.environ.get("APCA_API_SECRET_KEY", "")

        if not self._api_key or not self._secret_key:
            raise ValueError(
                "Alpaca credentials required. Set APCA_API_KEY_ID and "
                "APCA_API_SECRET_KEY environment variables, or pass "
                "api_key/secret_key to the constructor."
            )

        self._client = TradingClient(
            self._api_key,
            self._secret_key,
            paper=paper,
        )

        # Track submitted order IDs for polling
        self._submitted_order_ids: list[str] = []

    def submit(
        self, intent: OrderIntent, market: MarketSnapshot
    ) -> str:
        """Submit an order to Alpaca. Returns the Alpaca order ID."""
        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL

        if intent.order_type == "market":
            request = MarketOrderRequest(
                symbol=intent.instrument.symbol,
                qty=intent.qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        elif intent.order_type == "limit":
            if intent.limit_price is None:
                raise ValueError(
                    f"Limit order {intent.intent_id} requires a limit_price."
                )
            request = LimitOrderRequest(
                symbol=intent.instrument.symbol,
                qty=intent.qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=intent.limit_price,
            )
        else:
            raise ValueError(f"Unsupported order type: {intent.order_type}")

        order = self._client.submit_order(order_data=request)
        order_id = str(order.id)
        self._submitted_order_ids.append(order_id)
        return order_id

    def cancel(self, order_id: str) -> None:
        """Cancel a pending order."""
        self._client.cancel_order_by_id(order_id)

    def poll_fills(
        self, since_ts: str | None = None
    ) -> List[Fill]:
        """Poll for filled orders.

        Checks all tracked order IDs and returns Fill objects for those
        with status 'filled'. Once returned, fills are not returned again.
        """
        fills: list[Fill] = []
        remaining: list[str] = []

        for oid in self._submitted_order_ids:
            order = self._client.get_order_by_id(oid)

            if str(order.status) == "filled" or str(order.status.value) == "filled":
                filled_price = float(order.filled_avg_price or 0.0)
                filled_qty = float(order.filled_qty or 0.0)
                fill_ts = (
                    order.filled_at.isoformat()
                    if order.filled_at
                    else (order.updated_at.isoformat() if order.updated_at else "")
                )

                fills.append(
                    Fill(
                        order_id=str(order.id),
                        symbol=order.symbol,
                        side="buy" if str(order.side) in ("buy", "OrderSide.BUY") else "sell",
                        qty=filled_qty,
                        price=filled_price,
                        timestamp=fill_ts,
                    )
                )
                # Don't re-poll this order
            else:
                remaining.append(oid)

        self._submitted_order_ids = remaining
        return fills

    def get_order(self, order_id: str) -> BrokerOrder:
        """Fetch current status of an order."""
        order = self._client.get_order_by_id(order_id)

        # Map Alpaca status to our OrderStatus
        alpaca_status = str(order.status.value) if hasattr(order.status, "value") else str(order.status)
        status_map = {
            "new": "pending",
            "accepted": "pending",
            "pending_new": "pending",
            "partially_filled": "pending",
            "filled": "filled",
            "canceled": "cancelled",
            "expired": "cancelled",
            "rejected": "rejected",
            "pending_cancel": "pending",
            "pending_replace": "pending",
        }
        mapped_status = status_map.get(alpaca_status, "pending")

        return BrokerOrder(
            order_id=str(order.id),
            symbol=order.symbol,
            side="buy" if str(order.side) in ("buy", "OrderSide.BUY") else "sell",
            qty=float(order.qty or 0),
            order_type="market" if str(order.type) in ("market", "OrderType.MARKET") else "limit",
            limit_price=float(order.limit_price) if order.limit_price else None,
            status=mapped_status,
        )

    def get_account_equity(self) -> float:
        """Fetch current account equity from Alpaca."""
        account = self._client.get_account()
        return float(account.equity)

    def get_positions(self) -> dict[str, float]:
        """Fetch all open positions as {symbol: qty}."""
        positions = self._client.get_all_positions()
        return {
            p.symbol: float(p.qty)
            for p in positions
        }
