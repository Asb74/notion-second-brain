import json
import sqlite3

import pytest

from app.persistence.db import run_migrations
from app.persistence.order_training_repository import OrderTrainingRepository


def _repo() -> tuple[sqlite3.Connection, OrderTrainingRepository]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    return conn, OrderTrainingRepository(conn)


def test_create_example_serializa_json_unicode_y_lista() -> None:
    conn, repo = _repo()

    example_id = repo.create_example(
        gmail_id="gmail-1",
        numero_pedido="PED-1",
        source_file="pedido.pdf",
        pdf_text="texto ñ",
        extracted_json=[{"NumeroPedido": "PED-1", "Mercancia": "Naranja"}],
    )

    row = repo.get_example(example_id)
    assert row is not None
    assert row["status"] == "pending"
    assert "Naranja" in row["extracted_json"]
    assert json.loads(row["extracted_json"])[0]["Mercancia"] == "Naranja"
    conn.close()


def test_create_example_evitar_duplicados_por_clave_unica() -> None:
    conn, repo = _repo()
    repo.create_example("gmail-1", "PED-1", "pedido.pdf", "texto", {"ok": True})

    with pytest.raises(sqlite3.IntegrityError):
        repo.create_example("gmail-1", "PED-1", "pedido.pdf", "texto", {"ok": True})

    conn.close()


def test_update_corrected_json_cambia_a_reviewed_y_mark_status() -> None:
    conn, repo = _repo()
    example_id = repo.create_example("gmail-1", "PED-1", "pedido.pdf", "texto", {"ok": True})

    repo.update_corrected_json(example_id, {"ok": "corregido"}, notes="revisado")
    row = repo.get_example(example_id)
    assert row is not None
    assert row["status"] == "reviewed"
    assert row["notes"] == "revisado"
    assert json.loads(row["corrected_json"])["ok"] == "corregido"

    repo.mark_status(example_id, "approved")
    assert repo.get_example(example_id)["status"] == "approved"
    conn.close()
