"""Tests for AlpacaBrokerAdapter.

These tests mock the alpaca-py TradingClient so they run without
real API credentials. They verify the adapter correctly translates
between PolicyGate types and Alpaca SDK types.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from policygate_capital.adapters.broker import BrokerOrder, Fill
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import MarketSnapshot


# Skip all tests if alpaca-py is not installed
alpaca_available = True
try:
    import alpaca.trading.client  # noqa: F401
except ImportError:
    alpaca_available = False

pytestmark = pytest.mark.skipif(
    not alpaca_available,
    reason="alpaca-py not installed",
)


def _make_intent(
    side: str = "buy",
    order_type: str = "market",
    qty: float = 10.0,
    limit_price: float | None = None,
    symbol: str = "AAPL",
) -> OrderIntent:
    return OrderIntent(
        intent_id="test-001",
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


def _mock_alpaca_order(
    order_id: str = "alpaca-uuid-001",
    symbol: str = "AAPL",
    side: str = "buy",
    qty: float = 10.0,
    status: str = "filled",
    filled_qty: float = 10.0,
    filled_avg_price: float = 200.0,
    order_type: str = "market",
    limit_price: float | None = None,
    filled_at: str | None = "2026-02-24T00:00:01+00:00",
    updated_at: str | None = "2026-02-24T00:00:01+00:00",
) -> SimpleNamespace:
    """Create a mock Alpaca Order object."""
    ns = SimpleNamespace(
        id=order_id,
        symbol=symbol,
        side=SimpleNamespace(value=side),
        qty=qty,
        status=SimpleNamespace(value=status),
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
        type=SimpleNamespace(value=order_type),
        limit_price=limit_price,
        filled_at=SimpleNamespace(isoformat=lambda: filled_at) if filled_at else None,
        updated_at=SimpleNamespace(isoformat=lambda: updated_at) if updated_at else None,
    )
    return ns


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_submit_market_order(mock_client_cls):
    """Market order is submitted with correct Alpaca request."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.submit_order.return_value = _mock_alpaca_order()

    adapter = AlpacaBrokerAdapter()
    intent = _make_intent(side="buy", order_type="market", qty=10)
    market = _make_market()

    order_id = adapter.submit(intent, market)

    assert order_id == "alpaca-uuid-001"
    mock_client.submit_order.assert_called_once()
    call_kwargs = mock_client.submit_order.call_args
    request = call_kwargs.kwargs.get("order_data") or call_kwargs[1].get("order_data") or call_kwargs[0][0]
    assert request.symbol == "AAPL"
    assert request.qty == 10.0


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_submit_limit_order(mock_client_cls):
    """Limit order is submitted with correct limit price."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.submit_order.return_value = _mock_alpaca_order(
        order_type="limit", limit_price=210.0
    )

    adapter = AlpacaBrokerAdapter()
    intent = _make_intent(side="buy", order_type="limit", qty=5, limit_price=210.0)
    market = _make_market()

    order_id = adapter.submit(intent, market)

    assert order_id == "alpaca-uuid-001"
    call_kwargs = mock_client.submit_order.call_args
    request = call_kwargs.kwargs.get("order_data") or call_kwargs[1].get("order_data") or call_kwargs[0][0]
    assert request.limit_price == 210.0


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_submit_limit_without_price_raises(mock_client_cls):
    """Limit order without limit_price raises ValueError."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    adapter = AlpacaBrokerAdapter()
    intent = _make_intent(side="buy", order_type="limit", qty=5, limit_price=None)
    market = _make_market()

    with pytest.raises(ValueError, match="limit_price"):
        adapter.submit(intent, market)


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_cancel_order(mock_client_cls):
    """Cancel delegates to Alpaca cancel_order_by_id."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    adapter = AlpacaBrokerAdapter()
    adapter.cancel("order-123")

    mock_client.cancel_order_by_id.assert_called_once_with("order-123")


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_poll_fills_returns_filled_orders(mock_client_cls):
    """poll_fills returns Fill objects for filled orders."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    adapter = AlpacaBrokerAdapter()

    # Simulate two submitted orders
    mock_client.submit_order.side_effect = [
        _mock_alpaca_order(order_id="id-1", status="filled"),
        _mock_alpaca_order(order_id="id-2", status="new"),
    ]

    adapter.submit(_make_intent(), _make_market())
    adapter.submit(_make_intent(), _make_market())

    # Set up get_order_by_id to return different statuses
    mock_client.get_order_by_id.side_effect = [
        _mock_alpaca_order(order_id="id-1", status="filled", filled_qty=10.0, filled_avg_price=200.0),
        _mock_alpaca_order(order_id="id-2", status="new", filled_qty=0, filled_avg_price=0),
    ]

    fills = adapter.poll_fills()

    assert len(fills) == 1
    assert fills[0].order_id == "id-1"
    assert fills[0].qty == 10.0
    assert fills[0].price == 200.0

    # id-2 should still be tracked
    assert len(adapter._submitted_order_ids) == 1
    assert adapter._submitted_order_ids[0] == "id-2"


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_get_order_status_mapping(mock_client_cls):
    """get_order maps Alpaca statuses to BrokerOrder statuses."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    adapter = AlpacaBrokerAdapter()

    test_cases = [
        ("new", "pending"),
        ("accepted", "pending"),
        ("filled", "filled"),
        ("canceled", "cancelled"),
        ("rejected", "rejected"),
    ]

    for alpaca_status, expected_status in test_cases:
        mock_client.get_order_by_id.return_value = _mock_alpaca_order(
            status=alpaca_status
        )
        order = adapter.get_order("test-id")
        assert order.status == expected_status, (
            f"Alpaca '{alpaca_status}' should map to '{expected_status}', "
            f"got '{order.status}'"
        )


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_get_account_equity(mock_client_cls):
    """get_account_equity returns float equity."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_account.return_value = SimpleNamespace(equity="100000.50")

    adapter = AlpacaBrokerAdapter()
    equity = adapter.get_account_equity()

    assert equity == 100000.50


@patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret"})
@patch("policygate_capital.adapters.alpaca_broker.TradingClient")
def test_get_positions(mock_client_cls):
    """get_positions returns {symbol: qty} dict."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_all_positions.return_value = [
        SimpleNamespace(symbol="AAPL", qty="50"),
        SimpleNamespace(symbol="TSLA", qty="20"),
    ]

    adapter = AlpacaBrokerAdapter()
    positions = adapter.get_positions()

    assert positions == {"AAPL": 50.0, "TSLA": 20.0}


def test_missing_credentials_raises():
    """Adapter raises ValueError without credentials."""
    from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter

    with patch.dict(os.environ, {}, clear=True):
        # Remove any existing env vars
        os.environ.pop("APCA_API_KEY_ID", None)
        os.environ.pop("APCA_API_SECRET_KEY", None)

        with pytest.raises(ValueError, match="credentials"):
            AlpacaBrokerAdapter()
