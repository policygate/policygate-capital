"""Tradier broker adapter.

Wraps the Tradier REST API to implement the BrokerAdapter protocol.
Supports sandbox and live environments.

Credentials (environment variables):
  - TRADIER_TOKEN       — OAuth bearer token
  - TRADIER_ACCOUNT_ID  — Tradier account ID
  - TRADIER_ENV         — "sandbox" (default) or "live"

Install the optional dependency:
  pip install policygate-capital[tradier]
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from policygate_capital.adapters.broker import BrokerOrder, Fill
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import MarketSnapshot

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError as e:
    raise ImportError(
        "requests is required for the Tradier adapter. "
        "Install with: pip install policygate-capital[tradier]"
    ) from e

_BASE_URLS = {
    "sandbox": "https://sandbox.tradier.com/",
    "live": "https://api.tradier.com/",
}

# Tradier status -> BrokerOrder status
_STATUS_MAP: Dict[str, str] = {
    "pending": "pending",
    "open": "pending",
    "partially_filled": "pending",
    "filled": "filled",
    "expired": "cancelled",
    "canceled": "cancelled",
    "rejected": "rejected",
}


class TradierBrokerAdapter:
    """Tradier paper/live trading adapter.

    Implements the BrokerAdapter protocol: submit, cancel, poll_fills.
    Uses account-level order polling with per-order fallback.
    """

    def __init__(
        self,
        token: str | None = None,
        account_id: str | None = None,
        env: str | None = None,
    ) -> None:
        self._token = token or os.environ.get("TRADIER_TOKEN", "")
        self._account_id = account_id or os.environ.get("TRADIER_ACCOUNT_ID", "")
        env = env or os.environ.get("TRADIER_ENV", "sandbox")

        if not self._token:
            raise ValueError(
                "Tradier token required. Set TRADIER_TOKEN environment "
                "variable, or pass token= to the constructor."
            )
        if not self._account_id:
            raise ValueError(
                "Tradier account ID required. Set TRADIER_ACCOUNT_ID "
                "environment variable, or pass account_id= to the constructor."
            )
        if env not in ("sandbox", "live"):
            raise ValueError(
                f"TRADIER_ENV must be 'sandbox' or 'live', got '{env}'"
            )

        self._base_url = _BASE_URLS[env]

        # Build session with retry on 429 / 5xx
        self._session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        })

        # Track submitted order IDs for polling
        self._submitted_order_ids: list[str] = []

    # ── Thin HTTP layer (mock this for tests) ─────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request against the Tradier API.

        Returns the parsed JSON response body.
        Raises requests.HTTPError on non-2xx responses.
        """
        url = urljoin(self._base_url, path)
        resp = self._session.request(
            method, url, params=params, data=data, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ── BrokerAdapter protocol ────────────────────────────────────────

    def submit(
        self, intent: OrderIntent, market: MarketSnapshot
    ) -> str:
        """Submit an order to Tradier. Returns the Tradier order ID."""
        symbol = intent.instrument.symbol

        order_data: Dict[str, Any] = {
            "class": "equity",
            "symbol": symbol,
            "side": intent.side,
            "quantity": str(int(intent.qty)),
            "type": intent.order_type,
            "duration": "day",
            "tag": intent.intent_id,
        }

        if intent.order_type == "limit":
            if intent.limit_price is None:
                raise ValueError(
                    f"Limit order {intent.intent_id} requires a limit_price."
                )
            order_data["price"] = str(intent.limit_price)

        body = self._request(
            "POST",
            f"v1/accounts/{self._account_id}/orders",
            data=order_data,
        )

        order = body.get("order", {})
        order_id = str(order.get("id", ""))
        if not order_id:
            raise RuntimeError(
                f"Tradier did not return an order ID: {body}"
            )

        self._submitted_order_ids.append(order_id)
        return order_id

    def cancel(self, order_id: str) -> None:
        """Cancel a pending order."""
        self._request(
            "DELETE",
            f"v1/accounts/{self._account_id}/orders/{order_id}",
        )

    def poll_fills(
        self, since_ts: str | None = None
    ) -> List[Fill]:
        """Poll for filled orders.

        Primary: account-level GET /v1/accounts/{id}/orders
        Fallback: per-order polling if account-level fails.
        """
        try:
            return self._poll_fills_account_level()
        except Exception:
            return self._poll_fills_per_order()

    def _poll_fills_account_level(self) -> List[Fill]:
        """Account-level order polling (preferred)."""
        body = self._request(
            "GET",
            f"v1/accounts/{self._account_id}/orders",
        )

        orders_data = body.get("orders", {})
        # Tradier returns {"orders": {"order": [...]}} or {"orders": "null"}
        if not orders_data or orders_data == "null":
            return []

        raw_orders = orders_data.get("order", [])
        if isinstance(raw_orders, dict):
            raw_orders = [raw_orders]

        fills: list[Fill] = []
        remaining: list[str] = []
        tracked_set = set(self._submitted_order_ids)

        for raw in raw_orders:
            oid = str(raw.get("id", ""))
            if oid not in tracked_set:
                continue

            status = raw.get("status", "")
            if status == "filled":
                fills.append(Fill(
                    order_id=oid,
                    symbol=raw.get("symbol", ""),
                    side=raw.get("side", "buy"),
                    qty=float(raw.get("exec_quantity", raw.get("quantity", 0))),
                    price=float(raw.get("avg_fill_price", 0)),
                    timestamp=raw.get("last_fill_timestamp", raw.get("create_date", "")),
                ))
            elif status in ("pending", "open", "partially_filled"):
                remaining.append(oid)
            # rejected/cancelled/expired — drop from tracking

        self._submitted_order_ids = remaining
        return fills

    def _poll_fills_per_order(self) -> List[Fill]:
        """Per-order fallback polling."""
        fills: list[Fill] = []
        remaining: list[str] = []

        for oid in self._submitted_order_ids:
            try:
                bo = self.get_order(oid)
            except Exception:
                remaining.append(oid)
                continue

            if bo.status == "filled":
                fills.append(Fill(
                    order_id=bo.order_id,
                    symbol=bo.symbol,
                    side=bo.side,
                    qty=bo.qty,
                    price=0.0,  # per-order endpoint doesn't always have avg price
                    timestamp="",
                ))
            elif bo.status == "pending":
                remaining.append(oid)

        self._submitted_order_ids = remaining
        return fills

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """Fetch current status of an order."""
        body = self._request(
            "GET",
            f"v1/accounts/{self._account_id}/orders/{order_id}",
        )

        raw = body.get("order", {})
        tradier_status = raw.get("status", "pending")
        mapped_status = _STATUS_MAP.get(tradier_status, "pending")

        order_type = raw.get("type", "market")
        limit_price = None
        if order_type == "limit":
            limit_price = float(raw.get("price", 0))

        return BrokerOrder(
            order_id=str(raw.get("id", order_id)),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", "buy"),
            qty=float(raw.get("quantity", 0)),
            order_type=order_type,
            limit_price=limit_price,
            status=mapped_status,
        )
