"""Repository for browsing and maintaining ML training examples."""

from __future__ import annotations

import sqlite3


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
