"""Repository for assisted-learning email response examples."""

from __future__ import annotations

import re
import sqlite3


class TrainingRepository:
    """Data access layer for response training examples."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ensure_table()

    def ensure_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_response_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                sender_type TEXT,
                original_subject TEXT,
                original_body TEXT,
                response_text TEXT,
                created_at TEXT,
                keywords TEXT
            )
            """
        )
        self.conn.commit()

    def save_example(
        self,
        *,
        category: str,
        sender_type: str,
        original_subject: str,
        original_body: str,
        response_text: str,
        created_at: str,
        keywords: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO email_response_examples (
                category,
                sender_type,
                original_subject,
                original_body,
                response_text,
                created_at,
                keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                sender_type,
                original_subject,
                original_body,
                response_text,
                created_at,
                keywords,
            ),
        )
        self.conn.commit()

    def get_similar_examples(
        self,
        category: str,
        subject: str,
        body: str,
        sender_type: str,
        limit: int = 3,
    ) -> list[dict[str, str]]:
        """Return examples for category/sender ordered by subject keyword overlap."""
        del body  # kept for backward-compatible signature usage
        max_items = max(0, limit)
        if max_items == 0:
            return []

        subject_keywords = self._extract_keywords(subject)
        primary_rows = self.conn.execute(
            """
            SELECT original_subject, original_body, response_text
            FROM email_response_examples
            WHERE lower(trim(category)) = lower(trim(?))
              AND lower(trim(sender_type)) = lower(trim(?))
            """,
            (category or "", sender_type or ""),
        ).fetchall()

        selected = self._score_examples(primary_rows, subject_keywords)
        if len(selected) >= max_items:
            return selected[:max_items]

        fallback_rows = self.conn.execute(
            """
            SELECT original_subject, original_body, response_text
            FROM email_response_examples
            WHERE lower(trim(category)) = lower(trim(?))
              AND lower(trim(sender_type)) != lower(trim(?))
            """,
            (category or "", sender_type or ""),
        ).fetchall()

        fallback = self._score_examples(fallback_rows, subject_keywords)
        return (selected + fallback)[:max_items]

    def _score_examples(self, rows: list[sqlite3.Row], subject_keywords: set[str]) -> list[dict[str, str]]:
        scored: list[tuple[int, dict[str, str]]] = []
        for row in rows:
            row_subject = str(row["original_subject"] or "")
            overlap = len(subject_keywords.intersection(self._extract_keywords(row_subject)))
            if overlap <= 0:
                continue
            scored.append(
                (
                    overlap,
                    {
                        "original_subject": row_subject,
                        "original_body": str(row["original_body"] or ""),
                        "response_text": str(row["response_text"] or ""),
                    },
                )
            )

        return [example for _, example in sorted(scored, key=lambda item: item[0], reverse=True)]

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", (text or "").lower())
        stopwords = {
            "de",
            "la",
            "el",
            "los",
            "las",
            "y",
            "o",
            "en",
            "para",
            "por",
            "con",
            "del",
            "al",
            "un",
            "una",
            "re",
            "fw",
            "fwd",
            "rv",
        }
        return {token for token in tokens if len(token) >= 4 and token not in stopwords}
