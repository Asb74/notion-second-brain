import sqlite3

from app.persistence.db import run_migrations
from app.persistence.knowledge_repository import KnowledgeRepository
from app.services.knowledge_query_service import extract_raw_terms, extract_terms, query_knowledge


def _repo() -> KnowledgeRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    return KnowledgeRepository(conn)


def test_extract_terms_ignores_spanish_stopwords_and_dige_typo() -> None:
    assert extract_raw_terms("dige que tengo de mercadona") == ["dige", "que", "tengo", "de", "mercadona"]
    assert extract_terms("dige que tengo de mercadona") == ["mercadona"]


def test_query_filters_stopword_matches_and_ranks_title_first(tmp_path) -> None:
    repo = _repo()
    title_id = repo.create_item(
        title="Tarjeta Mercadona",
        content="Datos de compra y fidelización.",
        area="Compras",
        tipo="Nota",
    )
    attachment_note_id = repo.create_item(
        title="Tickets guardados",
        content="Recibos varios del mes.",
        area="Compras",
        tipo="Nota",
    )
    unrelated_id = repo.create_item(
        title="Ejercicios para 3ª Edad",
        content="Rutina que tengo pendiente de revisar.",
        area="Salud",
        tipo="Nota",
    )
    attachment_path = tmp_path / "ticket_mercadona.txt"
    attachment_path.write_text("Compra semanal en Mercadona con verduras", encoding="utf-8")
    repo.add_attachment(
        item_id=attachment_note_id,
        original_filename="ticket_mercadona.txt",
        stored_filename="ticket_mercadona.txt",
        stored_path=str(attachment_path),
        mime_type="text/plain",
        file_size=attachment_path.stat().st_size,
    )

    results = query_knowledge("dige que tengo de mercadona", repository=repo)

    assert [result["note_id"] for result in results] == [title_id, attachment_note_id]
    assert unrelated_id not in [result["note_id"] for result in results]
    assert "Mercadona" in results[0]["title"]
    assert "mercadona" in results[1]["snippet"].casefold()
