import sqlite3

import pytest

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

    row = conn.execute("SELECT * FROM ml_training_examples").fetchone()

    assert row is not None
    assert row["dataset"] == "email_response"
    assert row["label"] == "priority"
    assert row["source"] == "generated_response"


def test_save_training_example_rejects_unsupported_dataset() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = TrainingRepository(conn)

    with pytest.raises(ValueError, match="Dataset no soportado"):
        repo.save_training_example(dataset="invalid", input_text="hola")


def test_get_similar_examples_filters_by_category_sender_and_orders_by_match() -> None:
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
        original_subject="de para con y",
        original_body="Body 3",
        response_text="Respuesta 3",
        created_at="2026-01-01T00:00:00",
        keywords="",
    )
    repo.save_example(
        category="priority",
        sender_type="interno",
        original_subject="Consulta licencia interna",
        original_body="Body 4",
        response_text="Respuesta 4",
        created_at="2026-01-01T00:00:00",
        keywords="consulta, licencia, interna",
    )

    examples = repo.get_similar_examples(
        category="priority",
        subject="Consulta licencia urgente",
        body="Texto",
        sender_type="externo",
        limit=3,
    )

    assert len(examples) == 3
    assert examples[0]["original_subject"] == "Consulta urgente licencia obra"
    assert examples[1]["original_subject"] == "Consulta licencia"
    assert examples[2]["original_subject"] == "Consulta licencia interna"
    assert all(example["original_body"] != "Body 3" for example in examples)


def test_save_and_list_refinement_history() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = TrainingRepository(conn)

    first_id = repo.save_refinement_history(
        dataset="email_summary",
        input_original="EMAIL_BODY:\nPedido 123",
        output_original="Resumen inicial",
        user_instruction="Hazlo más breve",
        refined_output="Pedido 123 pendiente.",
    )
    second_id = repo.save_refinement_history(
        dataset="email_summary",
        input_original="EMAIL_BODY:\nPedido 123",
        output_original="Pedido 123 pendiente.",
        user_instruction="Incluye cliente",
        refined_output="Cliente ACME. Pedido 123 pendiente.",
    )

    rows = repo.list_refinement_history("email_summary", "EMAIL_BODY:\nPedido 123", limit=10)

    assert len(rows) == 2
    assert rows[0]["id"] == second_id
    assert rows[1]["id"] == first_id
    assert rows[0]["user_instruction"] == "Incluye cliente"


def test_save_refinement_history_rejects_unsupported_dataset() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = TrainingRepository(conn)

    with pytest.raises(ValueError, match="Dataset de refinamiento no soportado"):
        repo.save_refinement_history(
            dataset="email_classification",
            input_original="i",
            output_original="o",
            user_instruction="u",
            refined_output="r",
        )
