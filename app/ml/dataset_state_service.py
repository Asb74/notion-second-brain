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
                last_trained_at TEXT,
                examples_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT,
                last_trained_examples_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.conn.commit()

    def mark_dirty(self, dataset: str, examples_count: int | None = None) -> None:
        state = self.get_state(dataset)
        now = datetime.now(timezone.utc).isoformat()
        current_count = examples_count if examples_count is not None else self._count_examples(dataset)
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, examples_count, updated_at, last_trained_examples_count)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 1,
                examples_count = excluded.examples_count,
                updated_at = excluded.updated_at,
                last_trained_examples_count = ml_dataset_state.last_trained_examples_count
            """,
            (
                dataset,
                current_count,
                now,
                int(state["last_trained_examples_count"] if state else 0),
            ),
        )
        self.conn.commit()

    def mark_trained(self, dataset: str, examples_count: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        current_count = examples_count if examples_count is not None else self._count_examples(dataset)
        self.conn.execute(
            """
            INSERT INTO ml_dataset_state (dataset, dirty, last_trained_at, examples_count, last_error, updated_at, last_trained_examples_count)
            VALUES (?, 0, ?, ?, NULL, ?, ?)
            ON CONFLICT(dataset) DO UPDATE SET
                dirty = 0,
                last_trained_at = excluded.last_trained_at,
                examples_count = excluded.examples_count,
                last_error = NULL,
                updated_at = excluded.updated_at,
                last_trained_examples_count = excluded.last_trained_examples_count
            """,
            (dataset, now, current_count, now, current_count),
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

    def get_state(self, dataset: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM ml_dataset_state WHERE dataset = ?", (dataset,)).fetchone()

    def has_enough_new_examples(self, dataset: str, min_new_examples: int) -> bool:
        state = self.get_state(dataset)
        if state is None:
            return self._count_examples(dataset) >= min_new_examples
        return int(state["examples_count"] or 0) - int(state["last_trained_examples_count"] or 0) >= min_new_examples

    def _count_examples(self, dataset: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM ml_training_examples WHERE dataset = ?",
            (dataset,),
        ).fetchone()
        return int(row["total"] if row else 0)
