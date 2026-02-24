"""Strict YAML / JSON loading with Pydantic validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import ValidationError

from policygate_capital.models.policy import CapitalPolicy


def load_policy_yaml(path: str | Path) -> CapitalPolicy:
    """Load and validate a CapitalPolicy from a YAML file."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Policy YAML must parse to a mapping at top level.")
    try:
        return CapitalPolicy.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Policy validation failed: {e}") from e


def load_json(path: str | Path) -> Dict[str, Any]:
    """Load a JSON file and return it as a dict."""
    path = Path(path)
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("JSON fixture must be an object.")
    return obj
