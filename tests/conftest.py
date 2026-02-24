"""Shared fixtures for all tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policygate_capital.models.intent import OrderIntent
from policygate_capital.models.state import (
    ExecutionState,
    MarketSnapshot,
    PortfolioState,
)
from policygate_capital.util.io import load_policy_yaml

FIXTURES = Path(__file__).parent / "fixtures"


def _load_json_model(path, model_cls):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return model_cls.model_validate(raw)


@pytest.fixture
def base_policy():
    return load_policy_yaml(FIXTURES / "policies" / "base_enforce.yaml")


@pytest.fixture
def intent_small():
    return _load_json_model(FIXTURES / "intents" / "buy_small.json", OrderIntent)


@pytest.fixture
def intent_breach_position():
    return _load_json_model(
        FIXTURES / "intents" / "buy_breach_position_pct.json", OrderIntent
    )


@pytest.fixture
def intent_breach_gross():
    return _load_json_model(
        FIXTURES / "intents" / "buy_breach_gross_exposure.json", OrderIntent
    )


@pytest.fixture
def intent_spam():
    return _load_json_model(FIXTURES / "intents" / "spam_orders.json", OrderIntent)


@pytest.fixture
def portfolio_normal():
    return _load_json_model(
        FIXTURES / "states" / "portfolio_normal.json", PortfolioState
    )


@pytest.fixture
def portfolio_daily_loss():
    return _load_json_model(
        FIXTURES / "states" / "portfolio_daily_loss_breached.json", PortfolioState
    )


@pytest.fixture
def portfolio_drawdown():
    return _load_json_model(
        FIXTURES / "states" / "portfolio_drawdown_breached.json", PortfolioState
    )


@pytest.fixture
def market_simple():
    return _load_json_model(FIXTURES / "states" / "market_simple.json", MarketSnapshot)


@pytest.fixture
def execution_normal():
    return _load_json_model(
        FIXTURES / "states" / "execution_normal.json", ExecutionState
    )


@pytest.fixture
def execution_rate_global():
    return _load_json_model(
        FIXTURES / "states" / "execution_rate_limited_global.json", ExecutionState
    )


@pytest.fixture
def execution_rate_strategy():
    return _load_json_model(
        FIXTURES / "states" / "execution_rate_limited_strategy.json", ExecutionState
    )
