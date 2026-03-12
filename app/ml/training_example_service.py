"""Centralized save flow for training examples with deduplication and dataset-state updates."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from app.ml.dataset_state_service import DatasetStateService
from app.ml.training_validation import build_dedupe_signature, is_near_duplicate, normalize_text

logger = logging.getLogger(__name__)


class TrainingExampleService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.dataset_state_service = DatasetStateService(conn)

    def save_training_example_if_new(
        self,
        *,
        dataset: str,
        input_text: str,
        output_text: str | None = None,
        label: str | None = None,
        metadata: str | None = None,
        source: str = "manual",
        detect_near_duplicates: bool = False,
    ) -> dict[str, Any]:
        normalized_dataset = (dataset or "").strip()
        signature = build_dedupe_signature(normalized_dataset, input_text, output_text, label)
        if self._exists_duplicate(signature):
            logger.info("Ejemplo duplicado omitido: dataset=%s signature=%s", normalized_dataset, signature)
            return {"inserted": False, "reason": "duplicate"}

        if detect_near_duplicates and self._exists_near_duplicate(normalized_dataset, input_text):
            logger.info("Ejemplo casi duplicado omitido: dataset=%s", normalized_dataset)
            return {"inserted": False, "reason": "near_duplicate"}

        self.conn.execute(
            """
            INSERT INTO ml_training_examples (
                dataset,
                input_text,
                output_text,
                label,
                metadata,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (normalized_dataset, input_text, output_text, label, metadata, source),
        )
        self.conn.commit()
        examples_count = self._count_examples(normalized_dataset)
        self.dataset_state_service.mark_example_added(normalized_dataset)
        self.dataset_state_service.set_examples_count(normalized_dataset, examples_count)
        logger.info("Nuevo ejemplo añadido a %s", normalized_dataset)
        logger.info("Dataset %s marcado como dirty", normalized_dataset)
        return {"inserted": True, "reason": "inserted", "examples_count": examples_count}

    def save_email_response_feedback(
        self,
        *,
        input_text: str,
        output_text: str,
        category: str | None,
        sender_type: str | None,
        keywords: str | None,
        tone: str | None = None,
        edited_by_user: bool = False,
        source: str = "generated_response",
    ) -> dict[str, Any]:
        metadata = json.dumps(
            {
                "sender_type": sender_type or "",
                "category": category or "",
                "keywords": keywords or "",
                "tone": tone or "",
                "edited_by_user": bool(edited_by_user),
            },
            ensure_ascii=False,
        )
        return self.save_training_example_if_new(
            dataset="email_response",
            input_text=input_text,
            output_text=output_text,
            label=category,
            metadata=metadata,
            source=source,
            detect_near_duplicates=True,
        )

    def save_email_summary_feedback(
        self,
        *,
        input_text: str,
        output_text: str,
        corrected_by_user: bool = False,
        summary_type: str | None = None,
        source: str = "generated_summary",
    ) -> dict[str, Any]:
        metadata = json.dumps(
            {
                "corrected_by_user": bool(corrected_by_user),
                "summary_type": summary_type or "quick",
                "input_length": len(normalize_text(input_text)),
                "output_length": len(normalize_text(output_text)),
            },
            ensure_ascii=False,
        )
        return self.save_training_example_if_new(
            dataset="email_summary",
            input_text=input_text,
            output_text=output_text,
            metadata=metadata,
            source=source,
            detect_near_duplicates=True,
        )

    def _exists_duplicate(self, signature: dict[str, str]) -> bool:
        clauses = []
        params: list[str] = []
        for field, value in signature.items():
            clauses.append(f"LOWER(TRIM(COALESCE({field}, ''))) = ?")
            params.append(value)
        if not clauses:
            return False
        row = self.conn.execute(
            f"SELECT 1 FROM ml_training_examples WHERE {' AND '.join(clauses)} LIMIT 1",
            params,
        ).fetchone()
        return row is not None

    def _exists_near_duplicate(self, dataset: str, input_text: str) -> bool:
        rows = self.conn.execute(
            """
            SELECT input_text
            FROM ml_training_examples
            WHERE dataset = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (dataset,),
        ).fetchall()
        return any(is_near_duplicate(input_text, str(row["input_text"] or "")) for row in rows)

    def _count_examples(self, dataset: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM ml_training_examples WHERE dataset = ?",
            (dataset,),
        ).fetchone()
        return int(row["total"] if row else 0)
