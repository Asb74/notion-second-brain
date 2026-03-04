import sqlite3

from app.persistence.training_repository import TrainingRepository


def test_training_repository_creates_and_saves_examples() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = TrainingRepository(conn)

    repo.save_example(
        category="priority",
        sender_type="externo",
        original_subject="Consulta sobre licencia",
        original_body="Detalle del mensaje",
        response_text="Gracias por tu mensaje, vamos a revisar la solicitud y te respondemos.",
        created_at="2026-01-01T00:00:00",
        keywords="consulta, licencia",
    )

    row = conn.execute("SELECT * FROM email_response_examples").fetchone()

    assert row is not None
    assert row["category"] == "priority"
    assert row["sender_type"] == "externo"
    assert row["keywords"] == "consulta, licencia"


def test_get_similar_examples_filters_by_category_and_orders_by_match() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = TrainingRepository(conn)

    repo.save_example(
        category="priority",
        sender_type="externo",
        original_subject="Consulta urgente licencia obra",
        original_body="Body 1",
        response_text="Respuesta 1",
        created_at="2026-01-01T00:00:00",
        keywords="consulta, urgente, licencia, obra",
    )
    repo.save_example(
        category="priority",
        sender_type="externo",
        original_subject="Consulta licencia",
        original_body="Body 2",
        response_text="Respuesta 2",
        created_at="2026-01-01T00:00:00",
        keywords="consulta, licencia",
    )
    repo.save_example(
        category="priority",
        sender_type="externo",
        original_subject="Revisión expediente",
        original_body="Body 3",
        response_text="Respuesta 3",
        created_at="2026-01-01T00:00:00",
        keywords="revision, expediente",
    )
    repo.save_example(
        category="otros",
        sender_type="externo",
        original_subject="Consulta licencia",
        original_body="Body 4",
        response_text="Respuesta 4",
        created_at="2026-01-01T00:00:00",
        keywords="consulta, licencia",
    )

    examples = repo.get_similar_examples(
        category="priority",
        subject="Consulta licencia urgente",
        body="Texto",
        limit=3,
    )

    assert len(examples) == 3
    assert examples[0]["original_subject"] == "Consulta urgente licencia obra"
    assert examples[1]["original_subject"] == "Consulta licencia"
    assert all(example["original_body"] != "Body 4" for example in examples)
