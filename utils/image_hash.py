"""Content hashing helpers for image deduplication."""

from __future__ import annotations

import hashlib


def sha256_bytes(content: bytes) -> str:
    """Return lowercase SHA-256 hex digest for binary payload."""
    h = hashlib.sha256()
    h.update(content)
    return h.hexdigest()

