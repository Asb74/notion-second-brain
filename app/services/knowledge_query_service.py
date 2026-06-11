"""Deterministic local question search for Knowledge notes.

Phase 3A intentionally avoids external AI, embeddings, and network calls.  The
service extracts relevant terms from a natural-language question and ranks local
Knowledge notes with simple weighted text matches.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from typing import Any

from app.persistence.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)

_SIMPLE_SIGNS_RE = re.compile(r"[¿?¡!.,;:()\[\]{}\"'`´“”‘’/\\|<>+=*_~#@$%^&-]+")
_TOKEN_RE = re.compile(r"\b[\wáéíóúüñÁÉÍÓÚÜÑ]+\b", re.UNICODE)

_STOPWORDS = {
    "a",
    "acerca",
    "al",
    "algo",
    "ante",
    "aquel",
    "aquella",
    "aquello",
    "aqui",
    "as",
    "asi",
    "busca",
    "con",
    "contra",
    "cuando",
    "cual",
    "cuales",
    "dame",
    "de",
    "del",
    "desde",
    "dige",
    "dije",
    "dime",
    "donde",
    "el",
    "ella",
    "ellas",
    "ellos",
    "en",
    "entre",
    "era",
    "es",
    "esa",
    "ese",
    "eso",
    "esta",
    "este",
    "esto",
    "fue",
    "guarde",
    "habla",
    "hablan",
    "hay",
    "hice",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "mi",
    "mis",
    "o",
    "para",
    "por",
    "que",
    "renove",
    "se",
    "sobre",
    "son",
    "su",
    "sus",
    "tengo",
    "tiene",
    "tienes",
    "tienen",
    "un",
    "una",
    "unas",
    "unos",
    "y",
}

_FIELD_WEIGHTS = {
    "title": 5.0,
    "tags": 4.0,
    "attachment_names": 4.0,
    "content": 2.0,
    "indexed_text": 2.0,
    "area": 1.0,
    "topic": 1.0,
    "type": 1.0,
    "source_type": 1.0,
}

_STRONG_MATCH_FIELDS = ("title", "tags", "attachment_names", "content", "indexed_text")
_SNIPPET_FIELDS = ("content", "indexed_text", "attachment_names", "title", "tags")
_MIN_RAW_SCORE = 2.0


def _fold(text: str) -> str:
    """Return lowercase text without accents for robust local matching."""
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.casefold()


def normalize_text(text: str) -> str:
    """Normalize text by lowercasing and removing simple punctuation."""
    cleaned = _SIMPLE_SIGNS_RE.sub(" ", str(text or "").casefold())
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_raw_terms(question: str) -> list[str]:
    """Return normalized query tokens before stopword filtering."""
    normalized = normalize_text(question)
    return [match.group(0).strip() for match in _TOKEN_RE.finditer(normalized) if match.group(0).strip()]


def extract_terms(question: str) -> list[str]:
    """Extract relevant search terms from a natural-language question."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in extract_raw_terms(question):
        folded = _fold(raw)
        if not folded or folded in _STOPWORDS:
            continue
        if not folded.isdigit() and len(folded) < 3:
            continue
        if folded not in seen:
            terms.append(raw)
            seen.add(folded)
    return terms


def _row_value(row: sqlite3.Row | dict[str, Any], key: str) -> str:
    try:
        if isinstance(row, sqlite3.Row) and key not in row.keys():
            return ""
        value = row[key]
    except (KeyError, IndexError):
        return ""
    return str(value or "")


def _count_term(text: str, term: str) -> int:
    haystack = _fold(text)
    needle = _fold(term)
    if not haystack or not needle:
        return 0
    return haystack.count(needle)


def _has_strong_match(row: sqlite3.Row | dict[str, Any], terms: Sequence[str]) -> bool:
    return any(_count_term(_row_value(row, field), term) for field in _STRONG_MATCH_FIELDS for term in terms)


def _find_best_snippet_text(row: sqlite3.Row | dict[str, Any], terms: Sequence[str]) -> tuple[str, str, str]:
    for field in _SNIPPET_FIELDS:
        text = _row_value(row, field)
        folded = _fold(text)
        for term in terms:
            if _fold(term) in folded:
                return text, term, field
    return _row_value(row, "indexed_text") or _row_value(row, "content"), "", ""


def make_snippet(row: sqlite3.Row | dict[str, Any], terms: Sequence[str], context: int = 80) -> str:
    """Return a compact snippet around the best matching term."""
    text, term, field = _find_best_snippet_text(row, terms)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if not term:
        return text[:180] + ("..." if len(text) > 180 else "")
    folded_text = _fold(text)
    folded_term = _fold(term)
    index = folded_text.find(folded_term)
    if index < 0:
        return text[:180] + ("..." if len(text) > 180 else "")
    start = max(index - context, 0)
    end = min(index + len(term) + context, len(text))
    snippet = text[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    snippet = f"{prefix}{snippet}{suffix}"
    if field == "attachment_names":
        return f"Coincidencia en adjunto: {snippet}"
    return snippet


def _score_row(row: sqlite3.Row | dict[str, Any], terms: Sequence[str]) -> float:
    raw_score = 0.0
    matched_terms: set[str] = set()
    for term in terms:
        term_matched = False
        matched_outside_index = False
        for field, weight in _FIELD_WEIGHTS.items():
            if field == "indexed_text" and matched_outside_index:
                # indexed_text is a denormalized search payload containing title, tags,
                # content and attachment names. Avoid double-counting those stronger
                # fields; use it as the source of truth only for attachment body text
                # or other text that is not already represented elsewhere.
                continue
            count = _count_term(_row_value(row, field), term)
            if not count:
                continue
            raw_score += weight
            term_matched = True
            if field != "indexed_text":
                matched_outside_index = True
        if term_matched:
            matched_terms.add(_fold(term))
    if raw_score < _MIN_RAW_SCORE or not _has_strong_match(row, terms):
        return 0.0
    coverage_bonus = len(matched_terms) / max(len(terms), 1)
    # Keep scores readable in the UI while preserving ordering by raw relevance.
    normalized = raw_score / (raw_score + 8.0)
    return round(min(0.99, normalized + (coverage_bonus * 0.08)), 2)


def _result_from_row(row: sqlite3.Row | dict[str, Any], terms: Sequence[str], score: float) -> dict[str, Any]:
    return {
        "note_id": int(_row_value(row, "note_id") or _row_value(row, "id") or 0),
        "title": _row_value(row, "title"),
        "area": _row_value(row, "area"),
        "topic": _row_value(row, "topic"),
        "type": _row_value(row, "type"),
        "score": score,
        "snippet": make_snippet(row, terms),
    }


def query_knowledge(
    question: str,
    limit: int = 20,
    *,
    repository: KnowledgeRepository | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Find the most relevant local Knowledge notes for a free-text question.

    Callers may pass either a ``KnowledgeRepository`` or a SQLite connection.
    No external AI, embeddings, or network services are used.
    """
    cleaned_question = str(question or "").strip()
    logger.info('KNOWLEDGE_QUERY: question="%s"', cleaned_question)
    raw_terms = extract_raw_terms(cleaned_question)
    terms = extract_terms(cleaned_question)
    logger.info("KNOWLEDGE_QUERY: raw_terms=%s", raw_terms)
    logger.info("KNOWLEDGE_QUERY: relevant_terms=%s", terms)
    if not terms:
        logger.info("KNOWLEDGE_QUERY: filtered_results=0")
        logger.info("KNOWLEDGE_QUERY: results=0")
        return []

    repo = repository or (KnowledgeRepository(conn) if conn is not None else None)
    if repo is None:
        raise ValueError("query_knowledge necesita un KnowledgeRepository o una conexión SQLite")

    candidate_limit = max(int(limit or 20) * 10, 100)
    candidates = repo.search_query_candidates(terms, candidate_limit)
    scored: list[dict[str, Any]] = []
    for row in candidates:
        score = _score_row(row, terms)
        if score <= 0:
            continue
        scored.append(_result_from_row(row, terms, score))

    scored.sort(key=lambda item: (-float(item["score"]), str(item["title"]).casefold(), int(item["note_id"])))
    results = scored[: max(1, int(limit or 20))]
    logger.info("KNOWLEDGE_QUERY: filtered_results=%s", len(scored))
    logger.info("KNOWLEDGE_QUERY: results=%s", len(results))
    return results
