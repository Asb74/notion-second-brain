"""Utilities to extract recipients from forwarded email bodies."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HEADER_RE = re.compile(r"^\s*(de|from|para|to|cc)\s*:\s*(.*)$", re.IGNORECASE)
_MARKER_RE = re.compile(r"mensaje\s+original", re.IGNORECASE)


def _find_forwarded_block_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if _MARKER_RE.search(line):
            return index

    for index, line in enumerate(lines):
        header_match = _HEADER_RE.match(line)
        if not header_match:
            continue
        if header_match.group(1).lower() not in {"de", "from"}:
            continue

        lookahead = lines[index : index + 12]
        if any(
            (match := _HEADER_RE.match(candidate)) and match.group(1).lower() in {"para", "to"}
            for candidate in lookahead
        ):
            return index

    return -1


def extract_original_recipients(body_text: str) -> dict:
    """Extract original from/to/cc addresses from a forwarded message block."""
    if not body_text:
        return {}

    lines = body_text.splitlines()
    start_index = _find_forwarded_block_start(lines)
    if start_index < 0:
        return {}

    extracted: dict[str, list[str]] = {"from": [], "to": [], "cc": []}

    for line in lines[start_index:]:
        match = _HEADER_RE.match(line)
        if not match:
            continue

        header_name = match.group(1).lower()
        target = {"de": "from", "from": "from", "para": "to", "to": "to", "cc": "cc"}.get(header_name)
        if not target:
            continue

        for address in _EMAIL_RE.findall(match.group(2)):
            if address not in extracted[target]:
                extracted[target].append(address)

    result = {
        key: ", ".join(values)
        for key, values in extracted.items()
        if values
    }

    return result if result else {}
