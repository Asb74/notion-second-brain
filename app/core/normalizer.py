"""Text normalization utilities."""

from __future__ import annotations

import re

_SIGNATURE_MARKERS = (
    "saludos",
    "atentamente",
    "best regards",
    "kind regards",
    "--",
)


def normalize_newlines(text: str) -> str:
    """Normalize line endings to \n and trim surrounding whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def collapse_spaces(text: str) -> str:
    """Collapse repeated spaces and tabs while preserving line breaks."""
    lines = []
    for line in text.split("\n"):
        lines.append(re.sub(r"[ \t]+", " ", line).strip())
    return "\n".join(lines).strip()


def _strip_signature_conservative(text: str) -> str:
    """Remove trailing signature blocks conservatively for pasted emails."""
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        candidate = lines[i].strip().lower()
        if any(candidate.startswith(marker) for marker in _SIGNATURE_MARKERS):
            # Keep marker line only if it is very near start (avoid deleting full content)
            if i > max(2, len(lines) // 3):
                return "\n".join(lines[:i]).strip()
    return text


def normalize_text(raw_text: str, source: str) -> str:
    """Normalize text used for deduplication.

    Rules:
    - trim text
    - normalize line breaks
    - collapse repeated spaces
    - email source: attempt conservative signature stripping
    """
    text = normalize_newlines(raw_text)
    text = collapse_spaces(text)
    if source == "email_pasted":
        text = _strip_signature_conservative(text)
        text = collapse_spaces(text)
    return text
