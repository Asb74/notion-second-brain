import sqlite3

from app.ml.dataset_state_service import DatasetStateService
from app.ml.few_shot_learning_service import FewShotLearningService


def _setup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
    DatasetStateService(conn)
    return conn


def test_consolidar_aprendizaje_limpia_duplicados_y_actualiza_estado() -> None:
    conn = _setup_conn()
    conn.executemany(
        """
        INSERT INTO ml_training_examples(dataset, input_text, output_text, source)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("email_summary", "**Hola**", "**Resumen**", "manual"),
            ("email_summary", "Hola", "Resumen", "manual"),
            ("email_summary", "", "sin input", "manual"),
            ("email_summary", "texto válido", "", "manual"),
            ("email_summary", "Segundo", "Resultado", "manual"),
        ],
    )
    conn.commit()

    service = FewShotLearningService(conn)
    result = service.consolidar_aprendizaje("email_summary")

    assert result == {"total_valid": 2, "duplicates_removed": 1, "invalid_removed": 2}

    rows = conn.execute(
        "SELECT input_text, output_text FROM ml_training_examples WHERE dataset = 'email_summary' ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [("Hola", "Resumen"), ("Segundo", "Resultado")]

    state = conn.execute("SELECT model_status, pending_examples_count, last_trained_at FROM ml_dataset_state WHERE dataset = 'email_summary'").fetchone()
    assert state is not None
    assert state["model_status"] == "ready"
    assert state["pending_examples_count"] == 0
    assert state["last_trained_at"]


def test_consolidar_aprendizaje_rechaza_dataset_no_few_shot() -> None:
    conn = _setup_conn()
    service = FewShotLearningService(conn)

    try:
        service.consolidar_aprendizaje("email_classification")
    except ValueError as exc:
        assert "few-shot" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")


def test_mark_example_added_sets_learning_for_few_shot() -> None:
    conn = _setup_conn()
    state_service = DatasetStateService(conn)

    state_service.mark_example_added("email_response", count_as_pending=True)

    state = conn.execute("SELECT model_status FROM ml_dataset_state WHERE dataset = 'email_response'").fetchone()
    assert state is not None
    assert state["model_status"] == "learning"
