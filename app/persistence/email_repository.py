"""Repository for email persistence operations."""

from __future__ import annotations

import sqlite3
from typing import Sequence


class EmailRepository:
    """Data access for ingested emails."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ensure_table()

    def ensure_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                gmail_id TEXT PRIMARY KEY,
                thread_id TEXT,
                subject TEXT,
                sender TEXT,
                received_at TEXT,
                body_text TEXT,
                body_html TEXT,
                has_attachments INTEGER,
                raw_payload_json TEXT,
                processed_at TEXT,
                status TEXT,
                category TEXT DEFAULT 'pending'
            )
            """
        )
        self._ensure_column("category", "TEXT DEFAULT 'pending'")
        self.conn.commit()

    def _ensure_column(self, name: str, sql_type: str) -> None:
        columns = self.conn.execute("PRAGMA table_info(emails)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if name not in column_names:
            self.conn.execute(f"ALTER TABLE emails ADD COLUMN {name} {sql_type}")

    def get_emails_by_category(self, category: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT gmail_id, subject, sender, received_at, body_text, body_html, status, category
            FROM emails
            WHERE category = ?
            ORDER BY received_at DESC
            """,
            (category,),
        ).fetchall()

    def get_email_content(self, gmail_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT gmail_id, subject, sender, received_at, body_text, body_html, status, category
            FROM emails
            WHERE gmail_id = ?
            """,
            (gmail_id,),
        ).fetchone()

    def update_status(self, gmail_id: str, status: str) -> None:
        self.conn.execute("UPDATE emails SET status = ? WHERE gmail_id = ?", (status, gmail_id))
        self.conn.commit()

    def delete_emails(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM emails WHERE gmail_id IN ({placeholders})", tuple(ids))
        self.conn.commit()

    def bulk_update_status(self, ids: Sequence[str], status: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        params = [status, *ids]
        self.conn.execute(f"UPDATE emails SET status = ? WHERE gmail_id IN ({placeholders})", tuple(params))
        self.conn.commit()
