"""Tests for policygate-serve HTTP intake surface.

Each test starts a server in a background thread on an ephemeral port,
sends requests via urllib, and asserts on responses.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from policygate_capital.cli_serve import create_server

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = str(FIXTURES / "policies" / "base_enforce.yaml")
PORTFOLIO = str(FIXTURES / "states" / "portfolio_normal.json")
MARKET = str(FIXTURES / "states" / "market_simple.json")


def _start_server(tmp_path, *, token=None, audit_log=False, port=0):
    """Create and start a server on an ephemeral port. Returns (server, base_url)."""
    audit_path = str(tmp_path / "audit.jsonl") if audit_log else None
    server = create_server(
        policy_path=POLICY,
        portfolio_path=PORTFOLIO,
        market_path=MARKET,
        host="127.0.0.1",
        port=port,
        broker_name="sim",
        audit_log_path=audit_path,
        token=token,
    )
    # Use the actual bound port (0 = OS picks)
    actual_port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{actual_port}"
    return server, base_url


def _post_intent(base_url, body, *, headers=None, token=None):
    """POST to /intent and return (status_code, response_dict)."""
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/intent",
        data=raw,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(base_url, path, *, token=None):
    """GET a path and return (status_code, response_dict)."""
    req = urllib.request.Request(f"{base_url}{path}", method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _make_intent(intent_id="test-001", symbol="AAPL", qty=10):
    """Build a minimal intent body for POST /intent."""
    return {
        "intent": {
            "intent_id": intent_id,
            "timestamp": "2026-02-24T09:30:01Z",
            "strategy_id": "demo_strategy",
            "account_id": "acct_1",
            "instrument": {"symbol": symbol, "asset_class": "equity"},
            "side": "buy",
            "order_type": "market",
            "qty": qty,
        }
    }


# ── Tests ─────────────────────────────────────────────────────────────


def test_health_endpoint(tmp_path):
    """GET /health returns status, run_id, policy_hash, positions_count."""
    server, base_url = _start_server(tmp_path)
    try:
        code, body = _get(base_url, "/health")
        assert code == 200
        assert body["status"] == "ok"
        assert "run_id" in body
        assert len(body["policy_hash"]) == 64
        assert body["positions_count"] == 0
        assert body["kill_switch_active"] is False
    finally:
        server.shutdown()


def test_allow_intent(tmp_path):
    """POST valid intent that should ALLOW, returns Decision with eval_ms."""
    server, base_url = _start_server(tmp_path)
    try:
        code, body = _post_intent(base_url, _make_intent())
        assert code == 200
        assert body["decision"] == "ALLOW"
        assert body["intent_id"] == "test-001"
        assert body["eval_ms"] > 0
        assert body["violations"] == []
    finally:
        server.shutdown()


def test_deny_intent(tmp_path):
    """POST intent with missing price triggers SYS-001 DENY."""
    server, base_url = _start_server(tmp_path)
    try:
        # TSLA is not in market_simple.json
        code, body = _post_intent(base_url, _make_intent(symbol="TSLA"))
        assert code == 200
        assert body["decision"] == "DENY"
        rules = {v["rule_id"] for v in body["violations"]}
        assert "SYS-001" in rules
    finally:
        server.shutdown()


def test_market_snapshot_override(tmp_path):
    """POST with market_snapshot in body overrides startup market."""
    server, base_url = _start_server(tmp_path)
    try:
        # TSLA not in startup market → DENY without override
        code1, body1 = _post_intent(base_url, _make_intent(intent_id="t-001", symbol="TSLA"))
        assert body1["decision"] == "DENY"

        # With market override providing TSLA price → ALLOW
        intent_with_market = _make_intent(intent_id="t-002", symbol="TSLA")
        intent_with_market["market_snapshot"] = {
            "timestamp": "2026-02-24T09:30:00Z",
            "prices": {"TSLA": 400.0, "AAPL": 200.0},
        }
        code2, body2 = _post_intent(base_url, intent_with_market)
        assert body2["decision"] == "ALLOW"
    finally:
        server.shutdown()


def test_state_evolution(tmp_path):
    """POST multiple intents, verify positions change via /health."""
    server, base_url = _start_server(tmp_path)
    try:
        # Buy 10 AAPL
        _post_intent(base_url, _make_intent(intent_id="evo-001", qty=10))
        code, health = _get(base_url, "/health")
        assert health["positions_count"] == 1

        # Buy 5 more AAPL
        _post_intent(base_url, _make_intent(intent_id="evo-002", qty=5))
        code, health = _get(base_url, "/health")
        assert health["positions_count"] == 1  # still 1 symbol
        assert health["orders_last_60s_global"] == 2
    finally:
        server.shutdown()


def test_invalid_json(tmp_path):
    """POST malformed body returns 400."""
    server, base_url = _start_server(tmp_path)
    try:
        req = urllib.request.Request(
            f"{base_url}/intent",
            data=b"not json",
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req)
            assert False, "Expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            body = json.loads(e.read())
            assert body["error"] == "invalid_json"
    finally:
        server.shutdown()


def test_payload_too_large(tmp_path):
    """POST >64KB body returns 413."""
    server, base_url = _start_server(tmp_path)
    try:
        big_body = json.dumps({"intent": {"padding": "x" * 70_000}}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/intent",
            data=big_body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req)
            assert False, "Expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 413
            body = json.loads(e.read())
            assert body["error"] == "payload_too_large"
    finally:
        server.shutdown()


def test_auth_token(tmp_path):
    """Server with --token rejects unauthenticated requests with 401."""
    server, base_url = _start_server(tmp_path, token="secret-token-123")
    try:
        # No token → 401
        code, body = _get(base_url, "/health")
        assert code == 401
        assert body["error"] == "unauthorized"

        # Wrong token → 401
        code, body = _get(base_url, "/health", token="wrong-token")
        assert code == 401

        # Correct token → 200
        code, body = _get(base_url, "/health", token="secret-token-123")
        assert code == 200
        assert body["status"] == "ok"

        # POST with correct token → 200
        code, body = _post_intent(
            base_url, _make_intent(), token="secret-token-123"
        )
        assert code == 200
        assert body["decision"] == "ALLOW"
    finally:
        server.shutdown()


def test_audit_log_written(tmp_path):
    """POST with --audit-log writes events with eval_ms."""
    server, base_url = _start_server(tmp_path, audit_log=True)
    try:
        _post_intent(base_url, _make_intent())
        _post_intent(base_url, _make_intent(intent_id="test-002", qty=5))

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        events = [
            json.loads(l)
            for l in audit_path.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(events) == 2
        for e in events:
            assert "eval_ms" in e
            assert e["eval_ms"] > 0
            assert "run_id" in e
    finally:
        server.shutdown()
