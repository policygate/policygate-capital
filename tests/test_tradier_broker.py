"""Tests for TradierBrokerAdapter.

These tests mock the _request() layer so they run without real API
credentials. They verify the adapter correctly translates between
PolicyGate types and Tradier API payloads.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from policygate_capital.adapters.broker import BrokerOrder, Fill
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import MarketSnapshot

# Skip all tests if requests is not installed
requests_available = True
try:
    import requests  # noqa: F401
except ImportError:
    requests_available = False

pytestmark = pytest.mark.skipif(
    not requests_available,
    reason="requests not installed",
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_intent(
    side: str = "buy",
    order_type: str = "market",
    qty: float = 10.0,
    limit_price: float | None = None,
    symbol: str = "AAPL",
    intent_id: str = "test-001",
) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        timestamp="2026-02-24T00:00:01Z",
        strategy_id="demo_strategy",
        account_id="acct_1",
        instrument={"symbol": symbol, "asset_class": "equity"},
        side=side,
        order_type=order_type,
        qty=qty,
        limit_price=limit_price,
    )


def _make_market() -> MarketSnapshot:
    return MarketSnapshot(
        timestamp="2026-02-24T00:00:00Z",
        prices={"AAPL": 200.0, "TSLA": 250.0},
    )


def _make_adapter():
    """Create a TradierBrokerAdapter with mocked credentials."""
    from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter

    with patch.dict(os.environ, {
        "TRADIER_TOKEN": "test-token",
        "TRADIER_ACCOUNT_ID": "test-account",
        "TRADIER_ENV": "sandbox",
    }):
        adapter = TradierBrokerAdapter()
    return adapter


# ── Tests ─────────────────────────────────────────────────────────────


def test_submit_market_order():
    """Market order is submitted with correct Tradier payload."""
    adapter = _make_adapter()
    adapter._request = MagicMock(return_value={
        "order": {"id": 12345, "status": "pending"}
    })

    intent = _make_intent(side="buy", order_type="market", qty=10)
    market = _make_market()

    order_id = adapter.submit(intent, market)

    assert order_id == "12345"
    adapter._request.assert_called_once()
    call_args = adapter._request.call_args
    assert call_args[0][0] == "POST"
    data = call_args[1]["data"]
    assert data["symbol"] == "AAPL"
    assert data["side"] == "buy"
    assert data["quantity"] == "10"
    assert data["type"] == "market"
    assert data["tag"] == "test-001"
    assert "price" not in data


def test_submit_limit_order():
    """Limit order includes price in the payload."""
    adapter = _make_adapter()
    adapter._request = MagicMock(return_value={
        "order": {"id": 12346, "status": "pending"}
    })

    intent = _make_intent(
        side="sell", order_type="limit", qty=5, limit_price=210.0,
    )
    market = _make_market()

    order_id = adapter.submit(intent, market)

    assert order_id == "12346"
    data = adapter._request.call_args[1]["data"]
    assert data["price"] == "210.0"
    assert data["type"] == "limit"
    assert data["side"] == "sell"


def test_submit_limit_without_price_raises():
    """Limit order without limit_price raises ValueError."""
    adapter = _make_adapter()

    intent = _make_intent(
        side="buy", order_type="limit", qty=5, limit_price=None,
    )
    market = _make_market()

    with pytest.raises(ValueError, match="limit_price"):
        adapter.submit(intent, market)


def test_cancel_order():
    """Cancel sends DELETE to the correct endpoint."""
    adapter = _make_adapter()
    adapter._request = MagicMock(return_value={"order": {"id": 999, "status": "ok"}})

    adapter.cancel("999")

    adapter._request.assert_called_once()
    call_args = adapter._request.call_args
    assert call_args[0][0] == "DELETE"
    assert "999" in call_args[0][1]


def test_poll_fills_returns_filled_orders():
    """poll_fills returns Fill objects for filled orders."""
    adapter = _make_adapter()
    adapter._submitted_order_ids = ["100", "101"]

    adapter._request = MagicMock(return_value={
        "orders": {
            "order": [
                {
                    "id": 100,
                    "symbol": "AAPL",
                    "side": "buy",
                    "status": "filled",
                    "quantity": "10",
                    "exec_quantity": "10",
                    "avg_fill_price": "200.50",
                    "last_fill_timestamp": "2026-02-24T00:00:01Z",
                },
                {
                    "id": 101,
                    "symbol": "TSLA",
                    "side": "sell",
                    "status": "open",
                    "quantity": "5",
                },
            ]
        }
    })

    fills = adapter.poll_fills()

    assert len(fills) == 1
    assert fills[0].order_id == "100"
    assert fills[0].symbol == "AAPL"
    assert fills[0].qty == 10.0
    assert fills[0].price == 200.50

    # 101 should still be tracked (open)
    assert adapter._submitted_order_ids == ["101"]


def test_poll_fills_fallback_per_order():
    """poll_fills falls back to per-order polling on account-level failure."""
    adapter = _make_adapter()
    adapter._submitted_order_ids = ["200"]

    call_count = 0

    def mock_request(method, path, **kwargs):
        nonlocal call_count
        call_count += 1
        if "orders" == path.split("/")[-1]:
            # Account-level endpoint fails
            raise RuntimeError("account-level failed")
        # Per-order endpoint succeeds
        return {
            "order": {
                "id": 200,
                "symbol": "AAPL",
                "side": "buy",
                "status": "filled",
                "quantity": "10",
                "type": "market",
            }
        }

    adapter._request = mock_request

    fills = adapter.poll_fills()

    assert len(fills) == 1
    assert fills[0].order_id == "200"
    assert adapter._submitted_order_ids == []


def test_get_order_status_mapping():
    """get_order maps Tradier statuses to BrokerOrder statuses."""
    adapter = _make_adapter()

    test_cases = [
        ("pending", "pending"),
        ("open", "pending"),
        ("partially_filled", "pending"),
        ("filled", "filled"),
        ("canceled", "cancelled"),
        ("expired", "cancelled"),
        ("rejected", "rejected"),
    ]

    for tradier_status, expected_status in test_cases:
        adapter._request = MagicMock(return_value={
            "order": {
                "id": 300,
                "symbol": "AAPL",
                "side": "buy",
                "status": tradier_status,
                "quantity": "10",
                "type": "market",
            }
        })

        order = adapter.get_order("300")
        assert order.status == expected_status, (
            f"Tradier '{tradier_status}' should map to '{expected_status}', "
            f"got '{order.status}'"
        )


def test_missing_token_raises():
    """Adapter raises ValueError without token."""
    from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter

    with patch.dict(os.environ, {
        "TRADIER_ACCOUNT_ID": "acct",
        "TRADIER_ENV": "sandbox",
    }, clear=True):
        os.environ.pop("TRADIER_TOKEN", None)
        with pytest.raises(ValueError, match="token"):
            TradierBrokerAdapter()


def test_missing_account_id_raises():
    """Adapter raises ValueError without account ID."""
    from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter

    with patch.dict(os.environ, {
        "TRADIER_TOKEN": "tok",
        "TRADIER_ENV": "sandbox",
    }, clear=True):
        os.environ.pop("TRADIER_ACCOUNT_ID", None)
        with pytest.raises(ValueError, match="account"):
            TradierBrokerAdapter()


def test_invalid_env_raises():
    """Adapter raises ValueError for invalid TRADIER_ENV."""
    from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter

    with patch.dict(os.environ, {
        "TRADIER_TOKEN": "tok",
        "TRADIER_ACCOUNT_ID": "acct",
        "TRADIER_ENV": "production",
    }):
        with pytest.raises(ValueError, match="sandbox.*live"):
            TradierBrokerAdapter()


def test_submit_tag_equals_intent_id():
    """submit() sets tag= to intent_id for traceability."""
    adapter = _make_adapter()
    adapter._request = MagicMock(return_value={
        "order": {"id": 500, "status": "pending"}
    })

    intent = _make_intent(intent_id="my-custom-intent-123")
    market = _make_market()

    adapter.submit(intent, market)

    data = adapter._request.call_args[1]["data"]
    assert data["tag"] == "my-custom-intent-123"


def test_poll_fills_empty_orders():
    """poll_fills handles empty/null orders response."""
    adapter = _make_adapter()
    adapter._submitted_order_ids = ["400"]

    adapter._request = MagicMock(return_value={"orders": "null"})

    fills = adapter.poll_fills()

    assert fills == []
    # Order ID preserved since we couldn't determine its status
    assert adapter._submitted_order_ids == ["400"]


# ── Integration test (skipped by default) ─────────────────────────────


@pytest.mark.skipif(
    not (os.environ.get("TRADIER_TOKEN") and os.environ.get("TRADIER_INTEGRATION") == "1"),
    reason="Set TRADIER_TOKEN and TRADIER_INTEGRATION=1 for integration tests",
)
def test_tradier_integration_submit_and_poll():
    """Integration test: submit a sandbox order and poll for fills.

    Requires real Tradier sandbox credentials.
    """
    from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter

    adapter = TradierBrokerAdapter()

    intent = _make_intent(
        side="buy", order_type="market", qty=1, symbol="AAPL",
    )
    market = _make_market()

    order_id = adapter.submit(intent, market)
    assert order_id

    order = adapter.get_order(order_id)
    assert order.status in ("pending", "filled")
