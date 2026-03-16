"""Repository for browsing and maintaining ML training examples."""

from __future__ import annotations

import sqlite3

from app.ml.dataset_rules import get_dataset_rule
from app.ml.training_validation import content_hash, normalize_text


class MLTrainingRepository:
    """Data access helpers for ``ml_training_examples``."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_datasets_summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                dataset,
                COUNT(*) AS total,
                MAX(created_at) AS last_updated
            FROM ml_training_examples
            GROUP BY dataset
            ORDER BY dataset
            """
        ).fetchall()

    def list_examples(
        self,
        dataset: str | None = None,
        label: str | None = None,
        source: str | None = None,
        search: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[str] = []

        if dataset:
            clauses.append("dataset = ?")
            params.append(dataset)
        if label:
            clauses.append("label = ?")
            params.append(label)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if search:
            clauses.append("(input_text LIKE ? OR output_text LIKE ? OR metadata LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend([term, term, term])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.conn.execute(
            f"""
            SELECT
                id,
                dataset,
                COALESCE(label, '') AS label,
                COALESCE(source, '') AS source,
                COALESCE(created_at, '') AS created_at,
                COALESCE(substr(replace(input_text, char(10), ' '), 1, 140), '') AS input_preview,
                COALESCE(substr(replace(output_text, char(10), ' '), 1, 140), '') AS output_preview
            FROM ml_training_examples
            {where_sql}
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            params,
        ).fetchall()

    def get_example(self, example_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, dataset, input_text, output_text, label, metadata, source, created_at
            FROM ml_training_examples
            WHERE id = ?
            """,
            (example_id,),
        ).fetchone()

    def delete_example(self, example_id: int) -> None:
        self.conn.execute("DELETE FROM ml_training_examples WHERE id = ?", (example_id,))
        self.conn.commit()

    def count_labels_by_dataset(self, dataset: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT COALESCE(label, '(sin etiqueta)') AS label, COUNT(*) AS total
            FROM ml_training_examples
            WHERE dataset = ?
            GROUP BY COALESCE(label, '(sin etiqueta)')
            ORDER BY total DESC, label ASC
            """,
            (dataset,),
        ).fetchall()

    def list_distinct_values(self, field: str, dataset: str | None = None) -> list[str]:
        if field not in {"dataset", "label", "source"}:
            return []

        query = f"SELECT DISTINCT COALESCE({field}, '') AS value FROM ml_training_examples"
        params: list[str] = []
        if dataset and field != "dataset":
            query += " WHERE dataset = ?"
            params.append(dataset)
        query += " ORDER BY value"
        rows = self.conn.execute(query, params).fetchall()
        return [str(row["value"]) for row in rows if str(row["value"]).strip()]

    def total_examples(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS total FROM ml_training_examples").fetchone()
        return int(row["total"] if row else 0)

    def get_dataset_summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                dataset,
                COUNT(*) AS total,
                COUNT(DISTINCT NULLIF(TRIM(COALESCE(label, '')), '')) AS distinct_labels,
                MAX(created_at) AS last_updated
            FROM ml_training_examples
            GROUP BY dataset
            ORDER BY dataset
            """
        ).fetchall()

    def get_label_distribution(self, dataset: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            WITH total_rows AS (
                SELECT COUNT(*) AS total
                FROM ml_training_examples
                WHERE dataset = ?
            )
            SELECT
                COALESCE(NULLIF(TRIM(label), ''), '(sin etiqueta)') AS label,
                COUNT(*) AS count,
                ROUND(CASE
                    WHEN total_rows.total = 0 THEN 0
                    ELSE (COUNT(*) * 100.0 / total_rows.total)
                END, 2) AS percentage
            FROM ml_training_examples, total_rows
            WHERE dataset = ?
            GROUP BY COALESCE(NULLIF(TRIM(label), ''), '(sin etiqueta)'), total_rows.total
            ORDER BY count DESC, label ASC
            """,
            (dataset, dataset),
        ).fetchall()

    def count_incomplete_examples(self, dataset: str) -> sqlite3.Row:
        return self.conn.execute(
            """
            SELECT
                SUM(CASE WHEN input_text IS NULL OR TRIM(input_text) = '' THEN 1 ELSE 0 END) AS missing_input,
                SUM(CASE WHEN output_text IS NULL OR TRIM(output_text) = '' THEN 1 ELSE 0 END) AS missing_output,
                SUM(CASE WHEN label IS NULL OR TRIM(label) = '' THEN 1 ELSE 0 END) AS missing_label,
                SUM(CASE
                    WHEN (output_text IS NULL OR TRIM(output_text) = '')
                     AND (label IS NULL OR TRIM(label) = '')
                    THEN 1 ELSE 0 END
                ) AS missing_output_or_label
            FROM ml_training_examples
            WHERE dataset = ?
            """,
            (dataset,),
        ).fetchone()

    def count_duplicate_examples(self, dataset: str) -> int:
        return len(self.list_duplicate_examples(dataset))

    def list_duplicate_examples(self, dataset: str) -> list[dict[str, int | str]]:
        rows = self.conn.execute(
            """
            SELECT id, input_text, output_text, label
            FROM ml_training_examples
            WHERE dataset = ?
            ORDER BY id ASC
            """,
            (dataset,),
        ).fetchall()

        seen: dict[tuple[str, ...], int] = {}
        duplicates: list[dict[str, int | str]] = []

        for index, row in enumerate(rows, start=1):
            key = self._duplicate_key(dataset, row)
            if key in seen:
                duplicates.append(
                    {
                        "label": str(row["label"] or ""),
                        "text": str(row["input_text"] or ""),
                        "original_index": seen[key],
                        "duplicate_index": index,
                        "original_id": int(rows[seen[key] - 1]["id"]),
                        "duplicate_id": int(row["id"]),
                    }
                )
                continue
            seen[key] = index

        return duplicates

    def remove_duplicate_examples(self, dataset: str) -> int:
        duplicates = self.list_duplicate_examples(dataset)
        duplicate_ids = [int(item["duplicate_id"]) for item in duplicates]
        if not duplicate_ids:
            return 0

        placeholders = ", ".join("?" for _ in duplicate_ids)
        self.conn.execute(
            f"DELETE FROM ml_training_examples WHERE id IN ({placeholders})",
            duplicate_ids,
        )
        self.conn.commit()
        return len(duplicate_ids)

    def get_quality_issues(self, dataset: str) -> list[str]:
        issues: list[str] = []
        total = self._dataset_total(dataset)
        if total < 30:
            issues.append(f"El dataset {dataset} tiene pocos ejemplos totales ({total}/30).")

        distribution = self.get_label_distribution(dataset)
        for row in distribution:
            label = str(row["label"])
            count = int(row["count"])
            pct = float(row["percentage"])
            if label != "(sin etiqueta)" and count < 5:
                issues.append(f"La etiqueta {label} tiene pocos ejemplos ({count}).")
            if pct > 60:
                issues.append(f"El dataset {dataset} está desbalanceado: {label} ocupa {pct:.1f}%.")

        missing = self.count_incomplete_examples(dataset)
        required_fields = self._required_fields_for_dataset(dataset)
        if "input_text" in required_fields and int(missing["missing_input"] or 0) > 0:
            issues.append(f"Hay {int(missing['missing_input'])} ejemplos sin input_text en {dataset}.")
        if "output_text" in required_fields and int(missing["missing_output"] or 0) > 0:
            issues.append(f"Hay {int(missing['missing_output'])} ejemplos sin output_text en {dataset}.")
        if "label" in required_fields and int(missing["missing_label"] or 0) > 0:
            issues.append(f"Hay {int(missing['missing_label'])} ejemplos sin label en {dataset}.")
        if "output_or_label" in required_fields and int(missing["missing_output_or_label"] or 0) > 0:
            issues.append(f"Hay {int(missing['missing_output_or_label'])} ejemplos sin output_text ni label en {dataset}.")

        duplicates = self.count_duplicate_examples(dataset)
        if duplicates > 0:
            issues.append(f"Hay {duplicates} ejemplos duplicados en {dataset}.")
        return issues

    def get_recommendations(self, dataset: str) -> list[str]:
        recommendations: list[str] = []
        total = self._dataset_total(dataset)
        if total < 30:
            recommendations.append(f"Añadir al menos {30 - total} ejemplos más a {dataset}.")

        distribution = self.get_label_distribution(dataset)
        for row in distribution:
            label = str(row["label"])
            count = int(row["count"])
            if label != "(sin etiqueta)" and count < 5:
                recommendations.append(f"Añadir al menos {5 - count} ejemplos más a {label}.")

        if self.count_duplicate_examples(dataset) > 0:
            recommendations.append(f"Revisar duplicados en {dataset}.")

        if self._has_required_incompletes(dataset):
            recommendations.append(f"No reentrenar {dataset} hasta completar ejemplos incompletos.")
        else:
            recommendations.append(f"Reentrenar {dataset} cuando todas las etiquetas tengan >=5 ejemplos.")
        return recommendations

    def _has_required_incompletes(self, dataset: str) -> bool:
        missing = self.count_incomplete_examples(dataset)
        required_fields = self._required_fields_for_dataset(dataset)
        checks = {
            "input_text": int(missing["missing_input"] or 0),
            "output_text": int(missing["missing_output"] or 0),
            "label": int(missing["missing_label"] or 0),
            "output_or_label": int(missing["missing_output_or_label"] or 0),
        }
        return any(checks[field] > 0 for field in required_fields)

    def _dataset_total(self, dataset: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM ml_training_examples WHERE dataset = ?",
            (dataset,),
        ).fetchone()
        return int(row["total"] if row else 0)

    def _required_fields_for_dataset(self, dataset: str) -> set[str]:
        rule = get_dataset_rule(dataset)
        required_fields: set[str] = set()
        if rule.required_input_text:
            required_fields.add("input_text")
        if rule.required_output_text:
            required_fields.add("output_text")
        if rule.required_label:
            required_fields.add("label")
        if not rule.required_output_text and not rule.required_label:
            required_fields.add("output_or_label")
        return required_fields

    @staticmethod
    def _duplicate_key(dataset: str, row: sqlite3.Row) -> tuple[str, ...]:
        if (dataset or "").strip() == "email_classification":
            return (
                content_hash(row["input_text"]),
                normalize_text(row["label"]),
            )

        return (
            content_hash(row["input_text"]),
            content_hash(row["output_text"]),
            normalize_text(row["label"]),
        )
