"""Dataset-level state for incremental/continuous learning."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


class DatasetStateService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ensure_table()

    def ensure_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ml_dataset_state (
                dataset TEXT PRIMARY KEY,
                dirty INTEGER NOT NULL DEFAULT 0,
                examples_count INTEGER NOT NULL DEFAULT 0,
                pending_examples_count INTEGER NOT NULL DEFAULT 0,
                last_trained_at TEXT,
                last_incremental_at TEXT,
                last_error TEXT,
                auto_learning_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                last_trained_examples_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._ensure_column("examples_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("pending_examples_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("last_trained_at", "TEXT")
        self._ensure_column("last_incremental_at", "TEXT")
        self._ensure_column("last_error", "TEXT")
        self._ensure_column("auto_learning_enabled", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("updated_at", "TEXT")
        self._ensure_column("last_trained_examples_count", "INTEGER NOT NULL DEFAULT 0")
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE ml_dataset_state SET updated_at = COALESCE(updated_at, ?)", (now,))
        self.conn.commit()

    def _ensure_column(self, name: str, sql_type: str) -> None:
        columns = self.conn.execute("PRAGMA table_info(ml_dataset_state)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if name not in column_names:
            self.conn.execute(f"ALTER TABLE ml_dataset_state ADD COLUMN {name} {sql_type}")

    def mark_dirty(self, dataset: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 1,
                updated_at = excluded.updated_at
            """,
            (dataset, now),
        )
        self.conn.commit()

    def mark_example_added(self, dataset: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, examples_count, pending_examples_count, updated_at, last_error)
            VALUES (?, 1, 1, 1, ?, NULL)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 1,
                examples_count = ml_dataset_state.examples_count + 1,
                pending_examples_count = ml_dataset_state.pending_examples_count + 1,
                updated_at = excluded.updated_at,
                last_error = NULL
            """,
            (dataset, now),
        )
        self.conn.commit()

    def mark_incremental_success(self, dataset: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, pending_examples_count, last_incremental_at, updated_at, last_error)
            VALUES (?, 1, 0, ?, ?, NULL)
            ON CONFLICT(dataset) DO UPDATE SET
                pending_examples_count = CASE
                    WHEN ml_dataset_state.pending_examples_count > 0 THEN ml_dataset_state.pending_examples_count - 1
                    ELSE 0
                END,
                last_incremental_at = excluded.last_incremental_at,
                updated_at = excluded.updated_at,
                last_error = NULL,
                dirty = CASE
                    WHEN ml_dataset_state.pending_examples_count > 1 THEN 1
                    ELSE 0
                END
            """,
            (dataset, now, now),
        )
        self.conn.commit()

    def mark_full_train_success(self, dataset: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (
                dataset,
                dirty,
                examples_count,
                pending_examples_count,
                last_trained_at,
                updated_at,
                last_error,
                last_trained_examples_count
            )
            VALUES (?, 0, ?, 0, ?, ?, NULL, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 0,
                examples_count = excluded.examples_count,
                pending_examples_count = 0,
                last_trained_at = excluded.last_trained_at,
                updated_at = excluded.updated_at,
                last_error = NULL,
                last_trained_examples_count = excluded.last_trained_examples_count
            """,
            (dataset, self._count_examples(dataset), now, now, self._count_examples(dataset)),
        )
        self.conn.commit()

    def mark_error(self, dataset: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, last_error, updated_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 1,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (dataset, error, now),
        )
        self.conn.commit()

    def reset_pending_examples(self, dataset: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, pending_examples_count, updated_at)
            VALUES (?, 0, 0, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 0,
                pending_examples_count = 0,
                updated_at = excluded.updated_at
            """,
            (dataset, now),
        )
        self.conn.commit()

    def set_examples_count(self, dataset: str, count: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        safe_count = max(0, int(count))
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, examples_count, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                examples_count = excluded.examples_count,
                updated_at = excluded.updated_at
            """,
            (dataset, safe_count, now),
        )
        self.conn.commit()

    def get_state(self, dataset: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM ml_dataset_state WHERE dataset = ?", (dataset,)).fetchone()

    def has_enough_new_examples(self, dataset: str, min_new_examples: int) -> bool:
        state = self.get_state(dataset)
        if state is None:
            return self._count_examples(dataset) >= min_new_examples
        pending = int(state["pending_examples_count"] or 0)
        if pending:
            return pending >= min_new_examples
        return int(state["examples_count"] or 0) - int(state["last_trained_examples_count"] or 0) >= min_new_examples

    def mark_trained(self, dataset: str, examples_count: int | None = None) -> None:
        if examples_count is not None:
            self.set_examples_count(dataset, examples_count)
        self.mark_full_train_success(dataset)

    def _count_examples(self, dataset: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM ml_training_examples WHERE dataset = ?",
            (dataset,),
        ).fetchone()
        return int(row["total"] if row else 0)
