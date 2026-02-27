"""Hash helpers."""

from __future__ import annotations

import hashlib


def compute_source_id(normalized_text: str, source: str) -> str:
    """Compute deterministic SHA-256 source id using normalized text and source."""
    payload = f"{normalized_text}||{source}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
