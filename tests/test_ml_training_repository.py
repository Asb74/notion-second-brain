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
        ("email_classification", "subj b\nbody b", "", "spam", "{}", "manual", "2026-01-02 10:00:00"),
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
    assert summary[0]["total"] == 2

    filtered = repo.list_examples(dataset="email_classification", label="spam", source="manual", search="subj")
    assert len(filtered) == 2
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
    assert labels[0]["total"] == 2

    repo.delete_example(1)
    assert repo.get_example(1) is None
