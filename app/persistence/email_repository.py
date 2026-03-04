"""Repository for email persistence operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
                category TEXT DEFAULT 'pending',
                type TEXT DEFAULT 'other',
                original_from TEXT DEFAULT '',
                original_to TEXT DEFAULT '',
                original_cc TEXT DEFAULT '',
                original_reply_to TEXT DEFAULT ''
            )
            """
        )
        self._ensure_column("category", "TEXT DEFAULT 'pending'")
        self._ensure_column("type", "TEXT DEFAULT 'other'")
        self._ensure_column("sender", "TEXT")
        self._ensure_column("subject", "TEXT")
        self._ensure_column("received_at", "TEXT")
        self._ensure_column("body_text", "TEXT")
        self._ensure_column("status", "TEXT")
        self._ensure_column("original_from", "TEXT DEFAULT ''")
        self._ensure_column("original_to", "TEXT DEFAULT ''")
        self._ensure_column("original_cc", "TEXT DEFAULT ''")
        self._ensure_column("original_reply_to", "TEXT DEFAULT ''")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_labels (
                gmail_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                labeled_at TEXT NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sender_rules (
                sender TEXT PRIMARY KEY,
                forced_label TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self.conn.commit()

    def _ensure_column(self, name: str, sql_type: str) -> None:
        columns = self.conn.execute("PRAGMA table_info(emails)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if name not in column_names:
            self.conn.execute(f"ALTER TABLE emails ADD COLUMN {name} {sql_type}")

    def get_emails_by_types(self, types: Sequence[str]) -> list[sqlite3.Row]:
        if not types:
            return []
        placeholders = ",".join("?" for _ in types)
        return self.conn.execute(
            f"""
            SELECT gmail_id, subject, sender, received_at, body_text, body_html, status, category, type,
                   original_from, original_to, original_cc, original_reply_to
            FROM emails
            WHERE type IN ({placeholders})
            ORDER BY received_at DESC
            """,
            tuple(types),
        ).fetchall()

    def get_email_content(self, gmail_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT gmail_id, subject, sender, received_at, body_text, body_html, status, category, type,
                   original_from, original_to, original_cc, original_reply_to
            FROM emails
            WHERE gmail_id = ?
            """,
            (gmail_id,),
        ).fetchone()

    def update_status(self, gmail_id: str, status: str) -> None:
        self.conn.execute("UPDATE emails SET status = ? WHERE gmail_id = ?", (status, gmail_id))
        self.conn.commit()

    def update_type(self, gmail_id: str, new_type: str) -> None:
        self.conn.execute("UPDATE emails SET type = ? WHERE gmail_id = ?", (new_type, gmail_id))
        self.conn.commit()

    def bulk_update_type(self, ids: Sequence[str], new_type: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        params = [new_type, *ids]
        self.conn.execute(f"UPDATE emails SET type = ? WHERE gmail_id IN ({placeholders})", tuple(params))
        self.conn.commit()

    def save_label(self, gmail_id: str, label: str, source: str = "user") -> None:
        labeled_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO email_labels (gmail_id, label, labeled_at, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(gmail_id) DO UPDATE SET
                label=excluded.label,
                labeled_at=excluded.labeled_at,
                source=excluded.source
            """,
            (gmail_id, label, labeled_at, source),
        )
        self.conn.commit()

    def save_labels_for_emails(self, ids: Sequence[str], label: str, source: str = "user") -> None:
        for gmail_id in ids:
            self.save_label(gmail_id, label, source=source)

    def register_sender_rule(self, sender: str, forced_label: str) -> None:
        normalized = (sender or "").strip().lower()
        if not normalized:
            return
        self.conn.execute(
            """
            INSERT INTO sender_rules (sender, forced_label, hits)
            VALUES (?, ?, 1)
            ON CONFLICT(sender) DO UPDATE SET
                forced_label=excluded.forced_label,
                hits=sender_rules.hits + 1
            """,
            (normalized, forced_label),
        )
        self.conn.commit()

    def find_forced_label_for_sender(self, sender: str) -> str | None:
        normalized = (sender or "").strip().lower()
        if not normalized:
            return None
        row = self.conn.execute(
            "SELECT forced_label, hits FROM sender_rules WHERE sender = ?",
            (normalized,),
        ).fetchone()
        if row and int(row["hits"] or 0) >= 2:
            return str(row["forced_label"])
        return None

    def get_labeled_dataset(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT e.gmail_id, e.subject, e.sender, e.body_text, l.label
            FROM email_labels l
            JOIN emails e ON e.gmail_id = l.gmail_id
            ORDER BY l.labeled_at ASC
            """
        ).fetchall()

    def count_labeled_examples(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM email_labels").fetchone()
        return int(row["count"] if row else 0)

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
