"""Federated local search across Knowledge and cached emails."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from app.persistence.knowledge_repository import KnowledgeRepository
from app.services.knowledge_query_service import (
    extract_phrases,
    extract_terms,
    normalize_text,
    query_knowledge,
)

logger = logging.getLogger(__name__)

_EMAIL_WEIGHTS = {
    "subject": 6.0,
    "sender": 4.0,
    "recipients": 4.0,
    "attachments": 5.0,
    "body": 2.0,
    "metadata": 1.0,
}


def emails_available(conn: sqlite3.Connection) -> bool:
    """Return True when the local emails cache table exists and contains rows."""
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'emails'").fetchone()
        if row is None:
            return False
        count_row = conn.execute("SELECT COUNT(1) AS total FROM emails").fetchone()
        return int(count_row["total"] if isinstance(count_row, sqlite3.Row) else count_row[0]) > 0
    except sqlite3.Error:
        return False


def search_federated(
    query: str,
    include_knowledge: bool = True,
    include_emails: bool = True,
    limit: int = 30,
    *,
    conn: sqlite3.Connection | None = None,
    knowledge_repository: KnowledgeRepository | None = None,
) -> list[dict[str, Any]]:
    """Search local Knowledge and cached emails without importing emails."""
    cleaned = str(query or "").strip()
    logger.info("FEDERATED_SEARCH: query=%s", cleaned)
    logger.info(
        "FEDERATED_SEARCH: include_knowledge=%s include_emails=%s",
        include_knowledge,
        include_emails,
    )
    if conn is None and knowledge_repository is None:
        raise ValueError("search_federated necesita una conexión SQLite o un KnowledgeRepository")

    per_source_limit = max(1, int(limit or 30))
    knowledge_results: list[dict[str, Any]] = []
    email_results: list[dict[str, Any]] = []

    if include_knowledge:
        try:
            raw_knowledge = query_knowledge(
                cleaned,
                limit=per_source_limit,
                repository=knowledge_repository,
                conn=conn if knowledge_repository is None else None,
            )
            knowledge_results = [_normalize_knowledge_result(item) for item in raw_knowledge]
        except Exception:  # noqa: BLE001
            logger.exception("FEDERATED_SEARCH: knowledge search failed")
    logger.info("FEDERATED_SEARCH: knowledge_results=%s", len(knowledge_results))

    if include_emails and conn is not None:
        try:
            email_results = _search_emails(cleaned, conn, per_source_limit)
        except Exception:  # noqa: BLE001
            logger.exception("FEDERATED_SEARCH: email search failed")
    logger.info("FEDERATED_SEARCH: email_results=%s", len(email_results))

    results = [*knowledge_results, *email_results]
    results.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("date") or "")), reverse=False)
    final_results = results[: max(1, int(limit or 30))]
    logger.info("FEDERATED_SEARCH: total=%s", len(final_results))
    return final_results


def _normalize_knowledge_result(item: dict[str, Any]) -> dict[str, Any]:
    note_id = int(item.get("note_id") or item.get("id") or 0)
    return {
        "source": "knowledge",
        "id": str(note_id),
        "note_id": note_id,
        "title": item.get("title") or "Sin título",
        "subtitle": item.get("area") or "",
        "date": item.get("topic") or item.get("updated_at") or "",
        "type": item.get("type") or "Knowledge",
        "score": float(item.get("score") or 0.0),
        "match_source": item.get("match_source") or "",
        "snippet": item.get("snippet") or "",
        "raw": dict(item),
    }


def _search_emails(query: str, conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    if not emails_available(conn):
        return []
    phrases = extract_phrases(query)
    terms = [] if phrases else extract_terms(query)
    if not terms and not phrases:
        return []
    rows = conn.execute(
        """
        SELECT gmail_id, subject, sender, real_sender, received_at, body_text, status, category, type,
               original_from, original_to, original_cc, original_reply_to, attachments_json, numero_pedido
        FROM emails
        ORDER BY received_at DESC
        LIMIT ?
        """,
        (max(int(limit) * 20, 300),),
    ).fetchall()
    scored = [_score_email_row(row, terms, phrases) for row in rows]
    matches = [item for item in scored if item is not None]
    matches.sort(key=lambda item: (-float(item["score"]), str(item.get("date") or "")), reverse=False)
    return matches[: max(1, int(limit))]


def _score_email_row(row: sqlite3.Row, terms: list[str], phrases: list[str]) -> dict[str, Any] | None:
    fields = _email_fields(row)
    score = 0.0
    matched_sources: list[str] = []
    needles = phrases or terms
    for needle in needles:
        matched = False
        for field, text in fields.items():
            if _contains(text, needle):
                score += _EMAIL_WEIGHTS[field]
                matched = True
                label = _match_label(field)
                if label not in matched_sources:
                    matched_sources.append(label)
        if not matched:
            return None
    if score <= 0:
        return None
    gmail_id = str(row["gmail_id"] or "").strip()
    sender = str(row["real_sender"] or row["sender"] or row["original_from"] or "").strip()
    return {
        "source": "email",
        "id": gmail_id,
        "title": str(row["subject"] or "Sin asunto"),
        "subtitle": sender,
        "date": str(row["received_at"] or ""),
        "type": "Email",
        "score": score,
        "match_source": ", ".join(matched_sources),
        "snippet": _make_email_snippet(fields, needles),
        "raw": dict(row),
    }


def _email_fields(row: sqlite3.Row) -> dict[str, str]:
    attachments = _attachment_names(str(row["attachments_json"] or "[]"))
    recipients = " ".join(str(row[key] or "") for key in ("original_to", "original_cc", "original_reply_to"))
    metadata = " ".join(str(row[key] or "") for key in ("received_at", "status", "category", "type", "numero_pedido"))
    return {
        "subject": str(row["subject"] or ""),
        "sender": " ".join(str(row[key] or "") for key in ("sender", "real_sender", "original_from")),
        "recipients": recipients,
        "attachments": attachments,
        "body": str(row["body_text"] or ""),
        "metadata": metadata,
    }


def _attachment_names(raw_json: str) -> str:
    try:
        data = json.loads(raw_json or "[]")
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, list):
        return ""
    names = []
    for item in data:
        if isinstance(item, dict):
            names.append(str(item.get("filename") or item.get("name") or ""))
    return " ".join(names)


def _contains(text: str, needle: str) -> bool:
    return normalize_text(needle) in normalize_text(text)


def _match_label(field: str) -> str:
    return {
        "subject": "asunto",
        "sender": "remitente",
        "recipients": "destinatarios",
        "attachments": "adjuntos",
        "body": "cuerpo",
        "metadata": "metadatos",
    }.get(field, field)


def _make_email_snippet(fields: dict[str, str], needles: list[str], context: int = 80) -> str:
    for field in ("subject", "attachments", "sender", "recipients", "body", "metadata"):
        text = re.sub(r"\s+", " ", fields.get(field, "")).strip()
        folded = normalize_text(text)
        for needle in needles:
            index = folded.find(normalize_text(needle))
            if index >= 0:
                start = max(index - context, 0)
                end = min(index + len(needle) + context, len(text))
                snippet = f"{'...' if start else ''}{text[start:end].strip()}{'...' if end < len(text) else ''}"
                return f"Coincidencia en {_match_label(field)}: {snippet}"
    return ""
