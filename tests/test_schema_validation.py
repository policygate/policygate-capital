"""Tests for policy YAML schema validation."""

from pathlib import Path

import pytest

from policygate_capital.util.io import load_policy_yaml

FIXTURES = Path(__file__).parent / "fixtures" / "policies"


def test_valid_enforce_policy_parses():
    policy = load_policy_yaml(FIXTURES / "base_enforce.yaml")
    assert policy.version == "0.1"
    assert policy.defaults.mode == "enforce"
    assert policy.defaults.decision == "deny"
    assert policy.limits.exposure.max_position_pct == 0.10


def test_valid_monitor_policy_parses():
    policy = load_policy_yaml(FIXTURES / "base_monitor.yaml")
    assert policy.defaults.mode == "monitor"


def test_overrides_symbol_parses():
    policy = load_policy_yaml(FIXTURES / "overrides_symbol.yaml")
    assert "AAPL" in policy.overrides.symbols
    assert policy.overrides.symbols["AAPL"].exposure.max_position_pct == 0.05


def test_overrides_strategy_parses():
    policy = load_policy_yaml(FIXTURES / "overrides_strategy.yaml")
    assert "mean_reversion_v1" in policy.overrides.strategies
    strat = policy.overrides.strategies["mean_reversion_v1"]
    assert strat.execution.max_orders_per_minute_by_strategy == 5


def test_invalid_missing_fields_raises():
    with pytest.raises(ValueError, match="Policy validation failed"):
        load_policy_yaml(FIXTURES / "invalid_missing_fields.yaml")


def test_invalid_types_raises():
    with pytest.raises(ValueError, match="Policy validation failed"):
        load_policy_yaml(FIXTURES / "invalid_types.yaml")


def test_extra_fields_rejected(tmp_path):
    policy_file = tmp_path / "bad.yaml"
    policy_file.write_text(
        """
version: "0.1"
timezone: "UTC"
defaults:
  mode: "enforce"
  decision: "deny"
limits:
  exposure:
    max_position_pct: 0.10
    max_gross_exposure_x: 2.0
  loss:
    daily_loss_limit_pct: 0.02
    max_drawdown_pct: 0.05
  execution:
    max_orders_per_minute_global: 20
    max_orders_per_minute_by_strategy: 10
  kill_switch:
    trip_on_rules: []
    trip_after_n_violations: 3
    violation_window_seconds: 300
sneaky_extra_key: "should fail"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Policy validation failed"):
        load_policy_yaml(policy_file)


def test_non_utc_timezone_rejected(tmp_path):
    policy_file = tmp_path / "bad_tz.yaml"
    policy_file.write_text(
        """
version: "0.1"
timezone: "US/Eastern"
defaults:
  mode: "enforce"
  decision: "deny"
limits:
  exposure:
    max_position_pct: 0.10
    max_gross_exposure_x: 2.0
  loss:
    daily_loss_limit_pct: 0.02
    max_drawdown_pct: 0.05
  execution:
    max_orders_per_minute_global: 20
    max_orders_per_minute_by_strategy: 10
  kill_switch:
    trip_on_rules: []
    trip_after_n_violations: 3
    violation_window_seconds: 300
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Policy validation failed"):
        load_policy_yaml(policy_file)
