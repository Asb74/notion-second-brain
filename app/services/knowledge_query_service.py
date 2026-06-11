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
from datetime import datetime
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

_STOPWORDS.update(
    {
        "archivo",
        "archivos",
        "buscar",
        "documento",
        "documentos",
        "guardé",
        "informacion",
        "información",
        "lleva",
        "llevan",
        "lleven",
        "nota",
        "notas",
        "receta",
        "recetas",
        "tengan",
        "qué",
        "dónde",
        "cuál",
        "cuáles",
    }
)

_FIELD_WEIGHTS = {
    "title": 6.0,
    "tags": 5.0,
    "attachment_names": 5.0,
    "area": 2.0,
    "topic": 2.0,
    "content": 2.0,
    "indexed_text": 2.0,
}

_MATCH_SOURCE_BY_FIELD = {
    "title": "título",
    "tags": "etiquetas",
    "attachment_names": "adjunto",
    "area": "metadatos",
    "topic": "metadatos",
    "content": "contenido",
    "indexed_text": "contenido",
}
_SOURCE_PRIORITY = {"título": 0, "etiquetas": 1, "adjunto": 2, "contenido": 3, "metadatos": 4}
_STRONG_MATCH_FIELDS = ("title", "tags", "attachment_names", "content", "indexed_text")
_SNIPPET_FIELDS = ("content", "indexed_text", "attachment_names", "title", "tags", "area", "topic")
_MIN_SCORE = 1.5
_PARTIAL_MATCH_SCORE = 0.5
_QUOTED_PHRASE_RE = re.compile(r'\"([^\"\\]*(?:\\.[^\"\\]*)*)\"|“([^”]+)”|‘([^’]+)’')


def _fold(text: str) -> str:
    """Return lowercase text without accents for robust local matching."""
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.casefold()


def normalize_text(text: str) -> str:
    """Normalize text by lowercasing, removing accents, and cleaning punctuation."""
    cleaned = _SIMPLE_SIGNS_RE.sub(" ", _fold(str(text or "")))
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_raw_terms(question: str) -> list[str]:
    """Return normalized query tokens before stopword filtering."""
    normalized = normalize_text(question)
    return [match.group(0).strip() for match in _TOKEN_RE.finditer(normalized) if match.group(0).strip()]


def extract_terms(question: str) -> list[str]:
    """Extract relevant normalized search terms from a natural-language question."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in extract_raw_terms(_remove_quoted_phrases(question)):
        normalized = normalize_search_token(raw)
        if not normalized or normalized in _STOPWORDS:
            continue
        if not normalized.isdigit() and len(normalized) < 3:
            continue
        if normalized not in seen:
            terms.append(normalized)
            seen.add(normalized)
    return terms


def _singularize_basic(token: str) -> str:
    """Apply conservative Spanish singular/plural normalization for search terms."""
    folded = _fold(token)
    if len(folded) <= 4 or folded.endswith("is"):
        return folded
    if folded.endswith("ces") and len(folded) > 5:
        return f"{folded[:-3]}z"
    if folded.endswith("es") and len(folded) > 5 and folded[-3] not in "aeiou":
        return folded[:-2]
    if folded.endswith(("os", "as")) and len(folded) > 5:
        return folded[:-1]
    if folded.endswith("s") and len(folded) > 6 and not folded.endswith(("is", "us")):
        return folded[:-1]
    return folded


def normalize_search_token(token: str) -> str:
    """Return the normalized representation used for local Knowledge matching."""
    cleaned = normalize_text(token)
    return _singularize_basic(cleaned) if cleaned else ""


def extract_phrases(question: str) -> list[str]:
    """Extract quoted exact phrases, normalized but not split into independent terms."""
    phrases: list[str] = []
    seen: set[str] = set()
    for match in _QUOTED_PHRASE_RE.finditer(str(question or "")):
        raw = next((group for group in match.groups() if group), "")
        phrase = normalize_text(raw)
        folded = _fold(phrase)
        if phrase and folded not in seen:
            phrases.append(phrase)
            seen.add(folded)
    return phrases


def _remove_quoted_phrases(question: str) -> str:
    return _QUOTED_PHRASE_RE.sub(" ", str(question or ""))


def _row_value(row: sqlite3.Row | dict[str, Any], key: str) -> str:
    try:
        if isinstance(row, sqlite3.Row) and key not in row.keys():
            return ""
        value = row[key]
    except (KeyError, IndexError):
        return ""
    return str(value or "")


def _normalized_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in extract_raw_terms(text):
        normalized = normalize_search_token(token)
        if normalized:
            tokens.append(normalized)
    return tokens


def _literal_count(text: str, term: str) -> int:
    normalized_term = normalize_search_token(term)
    if not normalized_term:
        return 0
    return sum(1 for token in _normalized_tokens(text) if token == normalized_term)


def _contains_exact_phrase(text: str, phrase: str) -> bool:
    normalized = normalize_text(text)
    return bool(phrase and phrase in normalized)


def _has_partial_match(text: str, term: str) -> bool:
    normalized = normalize_text(text)
    normalized_term = normalize_search_token(term)
    return bool(normalized and normalized_term and normalized_term in normalized and not _literal_count(text, normalized_term))


def _field_match_count(text: str, term: str) -> tuple[int, bool]:
    literal_count = _literal_count(text, term)
    if literal_count:
        return literal_count, False
    return 0, _has_partial_match(text, term)


def _best_source(current: str, candidate: str) -> str:
    if not current:
        return candidate
    return min((current, candidate), key=lambda source: _SOURCE_PRIORITY.get(source, 99))


def _has_relevant_literal_match(row: sqlite3.Row | dict[str, Any], terms: Sequence[str], phrases: Sequence[str]) -> bool:
    if any(
        _contains_exact_phrase(_row_value(row, field), phrase)
        for phrase in phrases
        for field in _FIELD_WEIGHTS
    ):
        return True
    return any(
        _literal_count(_row_value(row, field), term)
        for term in terms
        for field in _STRONG_MATCH_FIELDS
    )


def _find_best_snippet_text(
    row: sqlite3.Row | dict[str, Any], terms: Sequence[str], phrases: Sequence[str] = ()
) -> tuple[str, str, str]:
    for phrase in phrases:
        for field in _SNIPPET_FIELDS:
            text = _row_value(row, field)
            if _contains_exact_phrase(text, phrase):
                return text, phrase, field
    for field in _SNIPPET_FIELDS:
        text = _row_value(row, field)
        for term in terms:
            if _literal_count(text, term):
                return text, term, field
    return "", "", ""


def make_snippet(
    row: sqlite3.Row | dict[str, Any], terms: Sequence[str], phrases: Sequence[str] = (), context: int = 80
) -> str:
    """Return a compact snippet around the best relevant matching term or phrase."""
    text, needle, field = _find_best_snippet_text(row, terms, phrases)
    text = re.sub(r"\s+", " ", text).strip()
    source = _MATCH_SOURCE_BY_FIELD.get(field, "contenido")
    if not text or not needle:
        return ""
    folded_text = normalize_text(text)
    folded_needle = normalize_text(needle)
    index = folded_text.find(folded_needle)
    if index < 0:
        fallback_labels = {
            "title": "Coincidencia en título",
            "attachment_names": f"Coincidencia en adjunto: {text[:120]}",
            "tags": "Coincidencia en etiquetas",
            "area": "Coincidencia en metadatos",
            "topic": "Coincidencia en metadatos",
        }
        return fallback_labels.get(field, "Coincidencia en contenido")
    start = max(index - context, 0)
    end = min(index + len(needle) + context, len(text))
    snippet = text[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    snippet = f"{prefix}{snippet}{suffix}"
    if source == "adjunto":
        return f"Coincidencia en adjunto: {snippet}"
    if source == "etiquetas":
        return f"Coincidencia en etiquetas: {snippet}"
    if source == "título":
        return f"Coincidencia en título: {snippet}"
    if source == "metadatos":
        return f"Coincidencia en metadatos: {snippet}"
    return snippet


def _score_row(
    row: sqlite3.Row | dict[str, Any], terms: Sequence[str], phrases: Sequence[str] = ()
) -> tuple[float, str]:
    score = 0.0
    match_source = ""
    phrase_match_source = ""
    matched_literals = 0
    weak_added = False

    for phrase in phrases:
        phrase_matched = False
        for field, weight in _FIELD_WEIGHTS.items():
            if _contains_exact_phrase(_row_value(row, field), phrase):
                # Quoted phrases are exact user intent, so exact phrase matches outrank split-term fallbacks.
                score += weight * 2
                phrase_match_source = _best_source(phrase_match_source, _MATCH_SOURCE_BY_FIELD[field])
                match_source = _best_source(match_source, _MATCH_SOURCE_BY_FIELD[field])
                phrase_matched = True
        if phrase_matched:
            matched_literals += 1

    for term in terms:
        term_matched = False
        matched_outside_index = False
        for field, weight in _FIELD_WEIGHTS.items():
            if field == "indexed_text" and matched_outside_index:
                continue
            literal_count, partial = _field_match_count(_row_value(row, field), term)
            if literal_count:
                # One literal hit gets the requested field weight; repeated content hits add a small bounded boost.
                frequency_bonus = min(max(literal_count - 1, 0), 3) * 0.25 if field in {"content", "indexed_text"} else 0.0
                score += weight + frequency_bonus
                match_source = _best_source(match_source, _MATCH_SOURCE_BY_FIELD[field])
                term_matched = True
                if field != "indexed_text":
                    matched_outside_index = True
            elif partial and not weak_added:
                score += _PARTIAL_MATCH_SCORE
                match_source = _best_source(match_source, _MATCH_SOURCE_BY_FIELD[field])
                weak_added = True
        if term_matched:
            matched_literals += 1

    if score < _MIN_SCORE:
        return 0.0, ""
    if terms and len(terms) == 1 and not _has_relevant_literal_match(row, terms, phrases):
        return 0.0, ""
    if (terms or phrases) and matched_literals <= 0:
        return 0.0, ""
    return round(score, 2), phrase_match_source or match_source or "contenido"


def _source_rank(match_source: object) -> int:
    return _SOURCE_PRIORITY.get(str(match_source or ""), 99)


def _updated_timestamp(row_or_item: sqlite3.Row | dict[str, Any]) -> str:
    return _row_value(row_or_item, "updated_at") or _row_value(row_or_item, "created_at")


def _timestamp_sort_value(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _result_from_row(
    row: sqlite3.Row | dict[str, Any], terms: Sequence[str], phrases: Sequence[str], score: float, match_source: str
) -> dict[str, Any]:
    return {
        "note_id": int(_row_value(row, "note_id") or _row_value(row, "id") or 0),
        "title": _row_value(row, "title"),
        "area": _row_value(row, "area"),
        "topic": _row_value(row, "topic"),
        "type": _row_value(row, "type"),
        "score": score,
        "snippet": make_snippet(row, terms, phrases),
        "match_source": match_source,
        "updated_at": _updated_timestamp(row),
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
    phrases = extract_phrases(cleaned_question)
    terms = extract_terms(cleaned_question)
    phrase_fallback_terms = extract_terms(" ".join(phrases))
    normalized_terms = list(dict.fromkeys([*terms, *phrase_fallback_terms]))
    logger.info("KNOWLEDGE_QUERY: raw_terms=%s", raw_terms)
    logger.info("KNOWLEDGE_QUERY: normalized_terms=%s", normalized_terms)
    logger.info("KNOWLEDGE_QUERY: phrases=%s", phrases)
    logger.info("KNOWLEDGE_QUERY: min_score=%s", _MIN_SCORE)
    if not normalized_terms and not phrases:
        logger.info("KNOWLEDGE_QUERY: filtered_results=0")
        logger.info("KNOWLEDGE_QUERY: results=0")
        return []

    repo = repository or (KnowledgeRepository(conn) if conn is not None else None)
    if repo is None:
        raise ValueError("query_knowledge necesita un KnowledgeRepository o una conexión SQLite")

    candidate_limit = max(int(limit or 20) * 10, 100)
    candidates = repo.search_query_candidates([*phrases, *normalized_terms], candidate_limit)
    scored: list[dict[str, Any]] = []
    for row in candidates:
        score, match_source = _score_row(row, normalized_terms, phrases)
        if score <= 0:
            continue
        result = _result_from_row(row, normalized_terms, phrases, score, match_source)
        logger.info(
            "KNOWLEDGE_QUERY: result note_id=%s score=%s match_source=%s",
            result["note_id"],
            result["score"],
            result["match_source"],
        )
        scored.append(result)

    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            _source_rank(item.get("match_source")),
            -_timestamp_sort_value(item.get("updated_at")),
            str(item["title"]).casefold(),
            int(item["note_id"]),
        ),
        reverse=False,
    )
    results = scored[: max(1, int(limit or 20))]
    logger.info("KNOWLEDGE_QUERY: filtered_results=%s", len(scored))
    logger.info("KNOWLEDGE_QUERY: results=%s", len(results))
    return results
