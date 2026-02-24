"""Typed exceptions for the policy engine."""

from __future__ import annotations


class PolicyLoadError(Exception):
    """Raised when a policy file cannot be loaded or validated."""


class EvaluationError(Exception):
    """Raised on an unrecoverable evaluation failure."""
