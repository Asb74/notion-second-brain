"""Reusable helpers for training-data normalization and deduplication."""

from __future__ import annotations

import re
import hashlib
from typing import Any

from app.ml.dataset_rules import get_dataset_rule


def normalize_text(text: str | None) -> str:
    value = str(text or "").strip().lower()
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\n+", "\n", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def content_hash(text: str | None) -> str:
    """Stable hash for normalized content comparisons."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_dedupe_signature(
    dataset: str,
    input_text: str | None,
    output_text: str | None,
    label: str | None,
) -> dict[str, str]:
    rule = get_dataset_rule(dataset)
    normalized = {
        "dataset": (dataset or "").strip(),
        "input_text": normalize_text(input_text),
        "output_text": normalize_text(output_text),
        "label": normalize_text(label),
    }
    return {field: normalized[field] for field in rule.dedupe_on if field in normalized}


def is_near_duplicate(candidate_text: str | None, existing_text: str | None) -> bool:
    """Simple near-duplicate detector for future interactive learning workflows."""
    a = normalize_text(candidate_text)
    b = normalize_text(existing_text)
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens.intersection(b_tokens)) / max(len(a_tokens), len(b_tokens))
    return overlap >= 0.9


def parse_metadata(raw_metadata: str | None) -> dict[str, Any]:
    import json

    try:
        loaded = json.loads(raw_metadata or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
