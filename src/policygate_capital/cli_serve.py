"""CLI entry point: policygate-serve.

HTTP intake surface for the Capital Policy Engine. Callers POST order
intents and receive synchronous governance decisions.

Threading model (v0.1):
    A single threading.Lock serialises evaluate + state mutation + broker
    submit. This means a slow broker call blocks other requests (head-of-line
    blocking). Acceptable for v0.1; document it in architecture.md.

Security:
    Binds to 127.0.0.1 by default. For external exposure, put behind a
    reverse proxy (nginx, Caddy) with TLS. Use --token to enforce Bearer
    auth as a safety net against accidental 0.0.0.0 binds.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from policygate_capital.adapters.broker import BrokerAdapter, Fill
from policygate_capital.adapters.sim_broker import SimBrokerAdapter
from policygate_capital.engine.audit import build_audit_event, write_audit_event
from policygate_capital.engine.policy_engine import PolicyEngine
from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.util.io import load_json

MAX_BODY_BYTES = 65_536  # 64 KB


def _write_exec_event(
    path: Path,
    event_type: str,
    intent_id: str,
    order_id: str,
    *,
    run_id: str | None = None,
    policy_hash: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> None:
    """Append a single execution event (same format as runner.py)."""
    event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "intent_id": intent_id,
        "order_id": order_id,
    }
    if run_id is not None:
        event["run_id"] = run_id
    if policy_hash is not None:
        event["policy_hash"] = policy_hash
    if extra:
        event.update(extra)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def _apply_fill(portfolio: PortfolioState, fill: Fill) -> None:
    """Update portfolio positions after a fill."""
    current_qty = portfolio.positions.get(fill.symbol, 0.0)
    if fill.side == "buy":
        new_qty = current_qty + fill.qty
    else:
        new_qty = current_qty - fill.qty
    if abs(new_qty) < 1e-10:
        portfolio.positions.pop(fill.symbol, None)
    else:
        portfolio.positions[fill.symbol] = new_qty


class _ServerState:
    """Shared mutable state protected by a lock."""

    def __init__(
        self,
        engine: PolicyEngine,
        portfolio: PortfolioState,
        market: MarketSnapshot,
        execution: ExecutionState,
        broker: BrokerAdapter,
        run_id: str,
        audit_log_path: Optional[Path],
        exec_log_path: Optional[Path],
        token: Optional[str],
    ) -> None:
        self.engine = engine
        self.portfolio = portfolio
        self.market = market
        self.execution = execution
        self.broker = broker
        self.run_id = run_id
        self.audit_log_path = audit_log_path
        self.exec_log_path = exec_log_path
        self.token = token
        self.lock = threading.Lock()


class IntentHandler(BaseHTTPRequestHandler):
    """HTTP handler for POST /intent and GET /health."""

    server_state: _ServerState  # set by factory

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging."""
        pass

    # ── Helpers ────────────────────────────────────────────────────────

    def _send_json(self, code: int, body: dict) -> None:
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _check_auth(self) -> bool:
        """Return True if auth passes; sends 401 and returns False otherwise."""
        token = self.server_state.token
        if token is None:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return True
        self._send_json(401, {"error": "unauthorized", "message": "Invalid or missing Bearer token."})
        return False

    # ── GET ────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        if not self._check_auth():
            return

        if self.path == "/health":
            st = self.server_state
            with st.lock:
                body = {
                    "status": "ok",
                    "run_id": st.run_id,
                    "policy_hash": st.engine.policy_hash,
                    "positions_count": len(st.portfolio.positions),
                    "kill_switch_active": st.execution.kill_switch_active,
                    "orders_last_60s_global": st.execution.orders_last_60s_global,
                }
            self._send_json(200, body)
            return

        self._send_json(404, {"error": "not_found"})

    # ── POST ───────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        if not self._check_auth():
            return

        if self.path != "/intent":
            self._send_json(404, {"error": "not_found"})
            return

        # Content-Type check
        ct = self.headers.get("Content-Type", "")
        if "application/json" not in ct:
            self._send_json(400, {
                "error": "invalid_content_type",
                "message": "Content-Type must be application/json.",
            })
            return

        # Content-Length check
        cl_str = self.headers.get("Content-Length")
        if cl_str is None:
            self._send_json(400, {
                "error": "missing_content_length",
                "message": "Content-Length header is required.",
            })
            return

        try:
            content_length = int(cl_str)
        except ValueError:
            self._send_json(400, {
                "error": "invalid_content_length",
                "message": "Content-Length must be an integer.",
            })
            return

        if content_length > MAX_BODY_BYTES:
            self._send_json(413, {
                "error": "payload_too_large",
                "message": f"Request body exceeds {MAX_BODY_BYTES} bytes.",
            })
            return

        # Read and parse body
        raw = self.rfile.read(min(content_length, MAX_BODY_BYTES))
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {
                "error": "invalid_json",
                "message": str(exc),
            })
            return

        # Extract intent (required) and market_snapshot (optional)
        intent_data = payload.get("intent") if isinstance(payload, dict) else None
        if intent_data is None:
            self._send_json(400, {
                "error": "invalid_json",
                "message": "Request body must be an object with an 'intent' key.",
            })
            return

        try:
            intent = OrderIntent.model_validate(intent_data)
        except Exception as exc:
            self._send_json(400, {
                "error": "invalid_json",
                "message": f"Invalid OrderIntent: {exc}",
            })
            return

        market_override = payload.get("market_snapshot")
        if market_override is not None:
            try:
                market_snap = MarketSnapshot.model_validate(market_override)
            except Exception as exc:
                self._send_json(400, {
                    "error": "invalid_json",
                    "message": f"Invalid market_snapshot: {exc}",
                })
                return
        else:
            market_snap = None

        # Evaluate under lock
        st = self.server_state
        with st.lock:
            market = market_snap if market_snap is not None else st.market
            decision = st.engine.evaluate(intent, st.portfolio, market, st.execution)

            # Audit (before submit)
            if st.audit_log_path:
                event = build_audit_event(
                    decision=decision,
                    intent=intent,
                    portfolio=st.portfolio,
                    market=market,
                    execution=st.execution,
                    policy_hash=st.engine.policy_hash,
                    run_id=st.run_id,
                )
                write_audit_event(st.audit_log_path, event)

            # Submit if allowed/modified
            if decision.decision in ("ALLOW", "MODIFY"):
                effective = decision.modified_intent if decision.modified_intent else intent

                try:
                    order_id = st.broker.submit(effective, market)
                except Exception:
                    if st.exec_log_path:
                        _write_exec_event(
                            st.exec_log_path, "ORDER_REJECTED",
                            intent.intent_id, "",
                            run_id=st.run_id,
                            policy_hash=st.engine.policy_hash,
                            extra={"symbol": effective.instrument.symbol},
                        )
                    raise

                if st.exec_log_path:
                    _write_exec_event(
                        st.exec_log_path, "ORDER_SUBMITTED",
                        intent.intent_id, order_id,
                        run_id=st.run_id,
                        policy_hash=st.engine.policy_hash,
                        extra={
                            "symbol": effective.instrument.symbol,
                            "side": effective.side,
                            "qty": effective.qty,
                            "order_type": effective.order_type,
                        },
                    )

                # Apply fills
                fills = st.broker.poll_fills(since_ts=intent.timestamp)
                for fill in fills:
                    _apply_fill(st.portfolio, fill)
                    if st.exec_log_path:
                        _write_exec_event(
                            st.exec_log_path, "ORDER_FILLED",
                            intent.intent_id, fill.order_id,
                            run_id=st.run_id,
                            policy_hash=st.engine.policy_hash,
                            extra={
                                "symbol": fill.symbol,
                                "side": fill.side,
                                "qty": fill.qty,
                                "price": fill.price,
                            },
                        )

                # Update execution counters
                st.execution.orders_last_60s_global += 1
                strat_orders = st.execution.orders_last_60s_by_strategy.get(
                    intent.strategy_id, 0
                )
                st.execution.orders_last_60s_by_strategy[intent.strategy_id] = (
                    strat_orders + 1
                )

            # Record violations in rolling window
            for v in decision.violations:
                st.execution.violations_last_window.append(
                    (intent.timestamp, v.rule_id)
                )

        # Respond with Decision
        self._send_json(200, decision.model_dump(mode="json"))

    # ── Catch-all for unsupported methods ──────────────────────────────

    def do_PUT(self) -> None:
        self._send_json(405, {"error": "method_not_allowed"})

    def do_DELETE(self) -> None:
        self._send_json(405, {"error": "method_not_allowed"})

    def do_PATCH(self) -> None:
        self._send_json(405, {"error": "method_not_allowed"})


def _create_broker(name: str) -> BrokerAdapter:
    """Lazy-import and instantiate the requested broker adapter."""
    if name == "sim":
        return SimBrokerAdapter()

    if name == "alpaca":
        try:
            from policygate_capital.adapters.alpaca_broker import AlpacaBrokerAdapter
        except ImportError:
            print(
                "Error: alpaca-py is not installed.\n"
                "  pip install policygate-capital[alpaca]",
                file=sys.stderr,
            )
            sys.exit(2)
        return AlpacaBrokerAdapter()

    if name == "tradier":
        try:
            from policygate_capital.adapters.tradier_broker import TradierBrokerAdapter
        except ImportError:
            print(
                "Error: requests is not installed.\n"
                "  pip install policygate-capital[tradier]",
                file=sys.stderr,
            )
            sys.exit(2)
        return TradierBrokerAdapter()

    print(f"Error: unknown broker '{name}'", file=sys.stderr)
    sys.exit(2)


def create_server(
    policy_path: str,
    portfolio_path: str,
    market_path: str,
    host: str = "127.0.0.1",
    port: int = 8100,
    broker_name: str = "sim",
    audit_log_path: Optional[str] = None,
    exec_log_path: Optional[str] = None,
    token: Optional[str] = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) a configured server. Useful for testing."""
    engine = PolicyEngine(policy_path)
    portfolio = PortfolioState.model_validate(load_json(portfolio_path))
    market = MarketSnapshot.model_validate(load_json(market_path))
    execution = ExecutionState()
    broker = _create_broker(broker_name)
    run_id = str(uuid.uuid4())

    audit_p = Path(audit_log_path) if audit_log_path else None
    exec_p = Path(exec_log_path) if exec_log_path else None

    # Clean existing logs
    for p in (audit_p, exec_p):
        if p is not None and p.exists():
            p.unlink()

    state = _ServerState(
        engine=engine,
        portfolio=portfolio,
        market=market,
        execution=execution,
        broker=broker,
        run_id=run_id,
        audit_log_path=audit_p,
        exec_log_path=exec_p,
        token=token,
    )

    # Attach state to handler class via a closure
    class Handler(IntentHandler):
        server_state = state

    server = ThreadingHTTPServer((host, port), Handler)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="policygate-serve",
        description="HTTP intake for the Capital Policy Engine.",
    )
    parser.add_argument(
        "--policy", required=True, help="Path to policy YAML file.",
    )
    parser.add_argument(
        "--portfolio", required=True, help="Path to initial portfolio state JSON.",
    )
    parser.add_argument(
        "--market", required=True, help="Path to market snapshot JSON.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default: 127.0.0.1). Use 0.0.0.0 with --token.",
    )
    parser.add_argument(
        "--port", type=int, default=8100,
        help="Listen port (default: 8100).",
    )
    parser.add_argument(
        "--broker", default="sim", choices=["sim", "alpaca", "tradier"],
        help="Broker adapter (default: sim).",
    )
    parser.add_argument(
        "--audit-log", default=None, help="Path to JSONL audit log output.",
    )
    parser.add_argument(
        "--exec-log", default=None, help="Path to JSONL execution event log.",
    )
    parser.add_argument(
        "--token", default=None,
        help="Bearer token for authentication. If set, all requests require "
             "Authorization: Bearer <token>.",
    )

    args = parser.parse_args(argv)

    try:
        server = create_server(
            policy_path=args.policy,
            portfolio_path=args.portfolio,
            market_path=args.market,
            host=args.host,
            port=args.port,
            broker_name=args.broker,
            audit_log_path=args.audit_log,
            exec_log_path=args.exec_log,
            token=args.token,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    print(f"policygate-serve listening on {args.host}:{args.port}")
    if args.token:
        print("  Bearer token authentication enabled")
    print(f"  Broker: {args.broker}")
    print(f"  POST /intent  — evaluate an order intent")
    print(f"  GET  /health  — server status")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
