"""Repository for email persistence operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Sequence


class EmailRepository:
    """Data access for ingested emails."""

    BASE_CATEGORIES: tuple[tuple[str, str], ...] = (
        ("priority", "Prioritarios"),
        ("order", "Pedidos"),
        ("subscription", "Suscripciones"),
        ("marketing", "Publicidad"),
        ("other", "Otros"),
    )

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
                real_sender TEXT,
                received_at TEXT,
                body_text TEXT,
                body_html TEXT,
                has_attachments INTEGER,
                raw_payload_json TEXT,
                attachments_json TEXT DEFAULT '[]',
                processed_at TEXT,
                status TEXT,
                category TEXT DEFAULT 'pending',
                type TEXT DEFAULT 'other',
                original_from TEXT DEFAULT '',
                original_to TEXT DEFAULT '',
                original_cc TEXT DEFAULT '',
                original_reply_to TEXT DEFAULT '',
                entities_json TEXT,
                pedido_json TEXT
            )
            """
        )
        self._ensure_column("category", "TEXT DEFAULT 'pending'")
        self._ensure_column("type", "TEXT DEFAULT 'other'")
        self._ensure_column("sender", "TEXT")
        self._ensure_column("real_sender", "TEXT")
        self._ensure_column("subject", "TEXT")
        self._ensure_column("received_at", "TEXT")
        self._ensure_column("body_text", "TEXT")
        self._ensure_column("status", "TEXT")
        self._ensure_column("original_from", "TEXT DEFAULT ''")
        self._ensure_column("original_to", "TEXT DEFAULT ''")
        self._ensure_column("original_cc", "TEXT DEFAULT ''")
        self._ensure_column("original_reply_to", "TEXT DEFAULT ''")
        self._ensure_column("attachments_json", "TEXT DEFAULT '[]'")
        self._ensure_column("entities_json", "TEXT")
        self._ensure_column("pedido_json", "TEXT")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_labels (
                gmail_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                source TEXT NOT NULL,
                labeled_at TEXT
            )
            """
        )
        self._ensure_email_labels_schema()
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_categories (
                name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                is_base INTEGER NOT NULL DEFAULT 0,
                created_at TEXT
            )
            """
        )
        self._seed_base_categories()
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sender_rules (
                sender TEXT PRIMARY KEY,
                forced_label TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT,
                local_path TEXT NOT NULL,
                size INTEGER,
                UNIQUE(gmail_id, filename, local_path)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ml_training_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset TEXT NOT NULL,
                input_text TEXT,
                output_text TEXT,
                label TEXT,
                metadata TEXT,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    def _ensure_column(self, name: str, sql_type: str) -> None:
        columns = self.conn.execute("PRAGMA table_info(emails)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if name not in column_names:
            self.conn.execute(f"ALTER TABLE emails ADD COLUMN {name} {sql_type}")

    def _ensure_email_labels_schema(self) -> None:
        columns = self.conn.execute("PRAGMA table_info(email_labels)").fetchall()
        if not columns:
            return
        column_names = {str(row["name"]) for row in columns}
        required_columns = {"gmail_id", "label", "source", "labeled_at"}
        if required_columns.issubset(column_names):
            return

        self.conn.execute("DROP TABLE IF EXISTS email_labels")
        self.conn.execute(
            """
            CREATE TABLE email_labels (
                gmail_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                source TEXT NOT NULL,
                labeled_at TEXT
            )
            """
        )

    def _seed_base_categories(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """
            INSERT INTO email_categories (name, display_name, is_base, created_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(name) DO UPDATE SET
                display_name=excluded.display_name,
                is_base=1
            """,
            [(name, display_name, now) for name, display_name in self.BASE_CATEGORIES],
        )

    def get_emails_by_types(self, types: Sequence[str]) -> list[sqlite3.Row]:
        if not types:
            return []
        placeholders = ",".join("?" for _ in types)
        return self.conn.execute(
            f"""
            SELECT gmail_id, subject, sender, real_sender, received_at, body_text, body_html, status, category, type,
                   original_from, original_to, original_cc, original_reply_to, attachments_json, entities_json, pedido_json
            FROM emails
            WHERE type IN ({placeholders})
            ORDER BY received_at DESC
            """,
            tuple(types),
        ).fetchall()

    def get_new_email_counts_by_type(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT type, COUNT(*) AS total
            FROM emails
            WHERE status = 'new'
            GROUP BY type
            """
        ).fetchall()
        return {str(row["type"] or "other"): int(row["total"] or 0) for row in rows}

    def get_email_content(self, gmail_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT gmail_id, subject, sender, real_sender, received_at, body_text, body_html, status, category, type,
                   original_from, original_to, original_cc, original_reply_to, attachments_json, entities_json, pedido_json
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
        rows = self.conn.execute(
            """
            SELECT
                id AS gmail_id,
                COALESCE(substr(input_text, 1, instr(input_text || char(10), char(10)) - 1), '') AS subject,
                '' AS sender,
                CASE
                    WHEN instr(input_text, char(10)) > 0 THEN substr(input_text, instr(input_text, char(10)) + 1)
                    ELSE ''
                END AS body_text,
                label
            FROM ml_training_examples
            WHERE dataset = 'email_classification'
              AND label IS NOT NULL
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        if rows:
            return rows
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

    def get_categories(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT name, display_name, is_base, created_at
            FROM email_categories
            ORDER BY is_base DESC, created_at ASC, display_name ASC
            """
        ).fetchall()

    def get_type_distribution(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT type, COUNT(1) AS cnt FROM emails GROUP BY type ORDER BY type"
        ).fetchall()
        return {str(row["type"] or "other"): int(row["cnt"] or 0) for row in rows}

    def get_category_names(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM email_categories ORDER BY is_base DESC, created_at ASC, name ASC"
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def count_categories(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM email_categories").fetchone()
        return int(row["count"] if row else 0)

    def get_all_emails_for_classification(self, exclude_user_labeled: bool = False) -> list[sqlite3.Row]:
        if exclude_user_labeled:
            return self.conn.execute(
                """
                SELECT e.gmail_id, e.subject, e.sender, e.body_text, e.type
                FROM emails e
                LEFT JOIN email_labels l ON l.gmail_id = e.gmail_id
                WHERE l.gmail_id IS NULL OR l.source != 'user'
                ORDER BY e.received_at DESC
                """
            ).fetchall()

        return self.conn.execute(
            """
            SELECT gmail_id, subject, sender, body_text, type
            FROM emails
            ORDER BY received_at DESC
            """
        ).fetchall()

    def bulk_update_email_types(self, updates: Sequence[tuple[str, str]]) -> None:
        if not updates:
            return
        self.conn.executemany("UPDATE emails SET type = ? WHERE gmail_id = ?", [(label, gmail_id) for gmail_id, label in updates])
        self.conn.commit()

    def create_category(self, name: str, display_name: str, is_base: int = 0) -> None:
        self.conn.execute(
            """
            INSERT INTO email_categories (name, display_name, is_base, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, display_name, is_base, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def rename_category(self, previous_name: str, next_name: str, next_display_name: str) -> None:
        self.conn.execute(
            """
            UPDATE email_categories
            SET name = ?, display_name = ?
            WHERE name = ?
            """,
            (next_name, next_display_name, previous_name),
        )
        self.conn.execute("DELETE FROM email_labels WHERE label = ?", (previous_name,))
        self.conn.execute("UPDATE emails SET type = ? WHERE type = ?", (next_name, previous_name))
        self.conn.commit()

    def delete_category(self, name: str) -> None:
        self.conn.execute("DELETE FROM email_labels WHERE label = ?", (name,))
        self.conn.execute("UPDATE emails SET type = 'other' WHERE type = ?", (name,))
        self.conn.execute("DELETE FROM email_categories WHERE name = ?", (name,))
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

    def save_attachment(
        self,
        gmail_id: str,
        filename: str,
        mime_type: str,
        local_path: str,
        size: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO email_attachments (gmail_id, filename, mime_type, local_path, size)
            VALUES (?, ?, ?, ?, ?)
            """,
            (gmail_id, filename, mime_type, local_path, size),
        )
        self.conn.commit()

    def get_attachments(self, gmail_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, gmail_id, filename, mime_type, local_path, size
            FROM email_attachments
            WHERE gmail_id = ?
            ORDER BY id ASC
            """,
            (gmail_id,),
        ).fetchall()
