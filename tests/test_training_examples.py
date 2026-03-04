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
