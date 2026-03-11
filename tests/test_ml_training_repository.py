import sqlite3

from app.persistence.ml_training_repository import MLTrainingRepository


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE ml_training_examples (
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
    rows = [
        ("email_classification", "subj a\nbody a", "", "spam", "{}", "manual", "2026-01-01 10:00:00"),
        ("email_classification", "subj a\nbody a", "", "spam", "{}", "manual", "2026-01-02 10:00:00"),
        ("email_classification", "subj c", "", "spam", "{}", "manual", "2026-01-03 10:00:00"),
        ("email_classification", "subj c", "", "priority", "{}", "manual", "2026-01-04 10:00:00"),
        ("email_response", "hola", "respuesta", "priority", '{"k":1}', "generated", "2026-01-03 10:00:00"),
    ]
    conn.executemany(
        """
        INSERT INTO ml_training_examples(dataset, input_text, output_text, label, metadata, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def test_list_summary_and_filters() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed(conn)
    repo = MLTrainingRepository(conn)

    summary = repo.list_datasets_summary()
    assert len(summary) == 2
    assert summary[0]["dataset"] == "email_classification"
    assert summary[0]["total"] == 4

    filtered = repo.list_examples(dataset="email_classification", label="spam", source="manual", search="subj")
    assert len(filtered) == 3
    assert filtered[0]["dataset"] == "email_classification"


def test_get_delete_and_label_counts() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed(conn)
    repo = MLTrainingRepository(conn)

    row = repo.get_example(1)
    assert row is not None
    assert row["dataset"] == "email_classification"

    labels = repo.count_labels_by_dataset("email_classification")
    assert labels[0]["label"] == "spam"
    assert labels[0]["total"] == 3

    repo.delete_example(1)
    assert repo.get_example(1) is None


def test_quality_metrics_and_recommendations() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed(conn)
    repo = MLTrainingRepository(conn)

    summary = repo.get_dataset_summary()
    email_class = next(row for row in summary if row["dataset"] == "email_classification")
    assert email_class["distinct_labels"] == 2

    distribution = repo.get_label_distribution("email_classification")
    assert distribution[0]["count"] == 3

    incompletes = repo.count_incomplete_examples("email_classification")
    assert incompletes["missing_label"] == 0

    duplicates = repo.count_duplicate_examples("email_classification")
    assert duplicates == 1

    issues = repo.get_quality_issues("email_classification")
    assert any("pocos ejemplos totales" in issue for issue in issues)
    assert any("desbalanceado" in issue for issue in issues)
    assert any("duplicados" in issue for issue in issues)

    recommendations = repo.get_recommendations("email_classification")
    assert any("Añadir al menos" in item for item in recommendations)
    assert any("Revisar duplicados" in item for item in recommendations)
