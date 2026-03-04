"""Repository for assisted-learning email response examples."""

from __future__ import annotations

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
