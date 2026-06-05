"""Repository for supervised order extraction training examples."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

ALLOWED_ORDER_TRAINING_STATUSES = {"pending", "reviewed", "approved", "discarded"}


class OrderTrainingRepository:
    """Data access helpers for ``order_training_examples``.

    ``corrected_json`` is intentionally isolated from operational order tables: it
    will be the trusted source for future supervised learning, but corrections do
    not modify saved ``pedidos`` or ``lineas`` records.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_example(
        self,
        gmail_id: str,
        numero_pedido: str,
        source_file: str,
        pdf_text: str,
        extracted_json: dict[str, Any] | list[Any],
    ) -> int:
        now = _utc_now_iso()
        cursor = self.conn.execute(
            """
            INSERT INTO order_training_examples (
                gmail_id,
                numero_pedido,
                source_file,
                pdf_text,
                extracted_json,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                (gmail_id or "").strip(),
                (numero_pedido or "").strip(),
                (source_file or "").strip(),
                pdf_text or "",
                _to_json_text(extracted_json),
                now,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_examples(self, status: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
        max_items = max(1, int(limit))
        normalized_status = (status or "").strip().lower()
        if normalized_status:
            _validate_status(normalized_status)
            return self.conn.execute(
                """
                SELECT id, gmail_id, numero_pedido, source_file, status, created_at, updated_at
                FROM order_training_examples
                WHERE status = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (normalized_status, max_items),
            ).fetchall()
        return self.conn.execute(
            """
            SELECT id, gmail_id, numero_pedido, source_file, status, created_at, updated_at
            FROM order_training_examples
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (max_items,),
        ).fetchall()

    def get_example(self, example_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                id,
                gmail_id,
                numero_pedido,
                source_file,
                pdf_text,
                extracted_json,
                corrected_json,
                status,
                notes,
                created_at,
                updated_at
            FROM order_training_examples
            WHERE id = ?
            """,
            (int(example_id),),
        ).fetchone()

    def update_corrected_json(
        self,
        example_id: int,
        corrected_json: dict[str, Any] | list[Any],
        notes: str = "",
    ) -> None:
        # The corrected JSON is the reliable label for future learning datasets.
        self.conn.execute(
            """
            UPDATE order_training_examples
            SET corrected_json = ?,
                notes = ?,
                status = 'reviewed',
                updated_at = ?
            WHERE id = ?
            """,
            (_to_json_text(corrected_json), notes or "", _utc_now_iso(), int(example_id)),
        )
        self.conn.commit()

    def mark_status(self, example_id: int, status: str) -> None:
        normalized_status = (status or "").strip().lower()
        _validate_status(normalized_status)
        self.conn.execute(
            """
            UPDATE order_training_examples
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized_status, _utc_now_iso(), int(example_id)),
        )
        self.conn.commit()

    def delete_example(self, example_id: int) -> None:
        self.conn.execute("DELETE FROM order_training_examples WHERE id = ?", (int(example_id),))
        self.conn.commit()


def _to_json_text(value: dict[str, Any] | list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _validate_status(status: str) -> None:
    if status not in ALLOWED_ORDER_TRAINING_STATUSES:
        raise ValueError(f"Estado de ejemplo de pedido no soportado: {status}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
