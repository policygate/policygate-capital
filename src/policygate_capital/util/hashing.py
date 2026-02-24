"""Policy hashing for audit trail integrity."""

from __future__ import annotations

import hashlib


def policy_hash(raw_text: str) -> str:
    """SHA-256 hash of the raw policy file content."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
