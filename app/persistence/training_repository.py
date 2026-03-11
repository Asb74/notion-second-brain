"""Repository for assisted-learning email response examples."""

from __future__ import annotations

import json
import re
import sqlite3


class TrainingRepository:
    """Data access layer for generic ML training datasets."""

    SUPPORTED_DATASETS = {
        "email_classification",
        "email_response",
        "email_summary",
        "task_detection",
        "calendar_event_generation",
    }

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ensure_table()

    def ensure_table(self) -> None:
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

    def save_training_example(
        self,
        dataset: str,
        input_text: str,
        output_text: str | None = None,
        label: str | None = None,
        metadata: str | None = None,
        source: str = "manual",
    ) -> None:
        normalized_dataset = (dataset or "").strip()
        if normalized_dataset not in self.SUPPORTED_DATASETS:
            raise ValueError(f"Dataset no soportado: {normalized_dataset}")

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
        del created_at
        metadata = json.dumps(
            {
                "sender_type": sender_type,
                "original_subject": original_subject,
                "keywords": keywords,
            },
            ensure_ascii=False,
        )
        combined_input = f"{original_subject or ''}\n{original_body or ''}".strip()
        self.save_training_example(
            dataset="email_response",
            input_text=combined_input,
            output_text=response_text,
            label=category,
            metadata=metadata,
            source="generated_response",
        )

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
            SELECT input_text, output_text, metadata
            FROM ml_training_examples
            WHERE dataset = 'email_response'
              AND lower(trim(label)) = lower(trim(?))
            """,
            (category or "",),
        ).fetchall()

        selected = self._score_examples(primary_rows, subject_keywords, sender_type=sender_type)
        if len(selected) >= max_items:
            return selected[:max_items]

        fallback_rows = self.conn.execute(
            """
            SELECT input_text, output_text, metadata
            FROM ml_training_examples
            WHERE dataset = 'email_response'
              AND lower(trim(label)) = lower(trim(?))
            """,
            (category or "",),
        ).fetchall()

        fallback = self._score_examples(fallback_rows, subject_keywords, sender_type=sender_type, strict_sender=False)
        return (selected + fallback)[:max_items]

    def _score_examples(
        self,
        rows: list[sqlite3.Row],
        subject_keywords: set[str],
        *,
        sender_type: str,
        strict_sender: bool = True,
    ) -> list[dict[str, str]]:
        scored: list[tuple[int, dict[str, str]]] = []
        for row in rows:
            parsed_metadata = self._parse_metadata(str(row["metadata"] or ""))
            row_sender_type = str(parsed_metadata.get("sender_type", "") or "")
            if strict_sender and row_sender_type.strip().lower() != (sender_type or "").strip().lower():
                continue
            if not strict_sender and row_sender_type.strip().lower() == (sender_type or "").strip().lower():
                continue

            full_input = str(row["input_text"] or "")
            row_subject, row_body = self._split_subject_body(full_input)
            overlap = len(subject_keywords.intersection(self._extract_keywords(row_subject)))
            if overlap <= 0:
                continue
            scored.append(
                (
                    overlap,
                    {
                        "original_subject": row_subject,
                        "original_body": row_body,
                        "response_text": str(row["output_text"] or ""),
                    },
                )
            )

        return [example for _, example in sorted(scored, key=lambda item: item[0], reverse=True)]

    @staticmethod
    def _parse_metadata(raw_metadata: str) -> dict[str, object]:
        try:
            loaded = json.loads(raw_metadata or "{}")
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _split_subject_body(full_input: str) -> tuple[str, str]:
        subject, separator, body = (full_input or "").partition("\n")
        if not separator:
            return full_input, ""
        return subject, body

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
