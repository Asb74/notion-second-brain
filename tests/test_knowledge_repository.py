import sqlite3

from app.persistence.db import run_migrations
from app.persistence.knowledge_repository import KnowledgeRepository


def _repo() -> KnowledgeRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    repo = KnowledgeRepository(conn)
    repo.create_area("Legacy Area")
    repo.create_item_type("Legacy Type")
    return repo


def test_topics_and_items_support_optional_topic() -> None:
    repo = _repo()
    area_id = int(repo.list_areas()[0]["id"])
    type_id = int(repo.list_item_types()[0]["id"])
    topic_id = repo.create_topic("Tema test", area_id=area_id, description="Descripción")

    item_without_topic = repo.create_item(
        title="Nota sin tema",
        content="Contenido",
        area_id=area_id,
        item_type_id=type_id,
        tags=["uno", " dos "],
    )
    item_with_topic = repo.create_item(
        title="Nota con tema",
        content="Contenido",
        area_id=area_id,
        item_type_id=type_id,
        topic_id=topic_id,
        tags=["tema"],
    )

    assert repo.get_item(item_without_topic)["topic_id"] is None
    row = repo.get_item(item_with_topic)
    assert row["topic_id"] == topic_id
    assert row["topic_name"] == "Tema test"
    assert [row["id"] for row in repo.list_items(area_id=area_id, topic_id=topic_id)] == [item_with_topic]
    assert repo.get_tags_for_item(item_without_topic) == ["dos", "uno"]


def test_update_topic_and_item_topic() -> None:
    repo = _repo()
    area_id = int(repo.list_areas()[0]["id"])
    type_id = int(repo.list_item_types()[0]["id"])
    topic_id = repo.create_topic("Inicial", area_id=area_id)
    item_id = repo.create_item("Nota", "Contenido", area_id, type_id)

    repo.update_topic(topic_id, name="Editado", area_id=area_id, description="Nueva", active=False)
    assert repo.list_topics(active_only=True) == []
    inactive = repo.list_topics(active_only=False)[0]
    assert inactive["name"] == "Editado"
    assert inactive["active"] == 0

    repo.update_item(item_id, "Nota", "Contenido", area_id, type_id, topic_id=topic_id, tags=[])
    assert repo.get_item(item_id)["topic_id"] == topic_id


def test_items_and_topics_support_global_area_tipo_text() -> None:
    repo = _repo()
    topic_id = repo.create_topic("Tema global", area="General", description="Descripción")
    item_id = repo.create_item(
        title="Nota global",
        content="Contenido",
        area="General",
        tipo="Nota",
        topic_id=topic_id,
        tags=["global"],
    )

    item = repo.get_item(item_id)
    assert item["area"] == "General"
    assert item["tipo"] == "Nota"
    assert item["area_name"] == "General"
    assert item["item_type_name"] == "Nota"
    assert item["topic_name"] == "Tema global"
    assert [row["id"] for row in repo.list_items(area="General", tipo="Nota")] == [item_id]
    assert [row["id"] for row in repo.list_topics(area="General")] == [topic_id]


def test_knowledge_attachment_crud() -> None:
    repo = _repo()
    item_id = repo.create_item(title="Nota con adjunto", content="Contenido")

    attachment_id = repo.add_attachment(
        item_id=item_id,
        original_filename="documento.pdf",
        stored_filename="20260610_documento.pdf",
        stored_path="/tmp/20260610_documento.pdf",
        mime_type="application/pdf",
        file_size=2048,
    )

    attachment = repo.get_attachment(attachment_id)
    assert attachment is not None
    assert attachment["item_id"] == item_id
    assert attachment["original_filename"] == "documento.pdf"
    assert attachment["stored_filename"] == "20260610_documento.pdf"
    assert attachment["stored_path"] == "/tmp/20260610_documento.pdf"
    assert attachment["mime_type"] == "application/pdf"
    assert attachment["file_size"] == 2048
    assert attachment["source_type"] == "manual"
    assert [row["id"] for row in repo.list_attachments(item_id)] == [attachment_id]

    repo.delete_attachment(attachment_id)

    assert repo.get_attachment(attachment_id) is None
    assert repo.list_attachments(item_id) == []


def test_exists_evernote_duplicate_uses_title_created_and_source() -> None:
    repo = _repo()
    repo.create_item(
        title="Nota Evernote",
        content="Contenido",
        source_type="evernote",
        source_id="20240101T120000Z",
    )
    repo.create_item(
        title="Nota manual",
        content="Contenido",
        source_type="manual",
        source_id="20240101T120000Z",
    )

    assert repo.exists_evernote_duplicate("Nota Evernote", "20240101T120000Z") is True
    assert repo.exists_evernote_duplicate("Nota Evernote", "20240102T120000Z") is False
    assert repo.exists_evernote_duplicate("Nota manual", "20240101T120000Z") is False


def test_automatic_knowledge_sources_create_empty_summary() -> None:
    repo = _repo()

    evernote_id = repo.create_item(
        title="Receta Evernote",
        content="migas (por persona)\nPan ...\nPimiento ...",
        source_type="evernote",
        summary="migas (por persona)\nPan ...\nPimiento ...",
    )
    email_id = repo.create_item(
        title="Email importado",
        content="Contenido completo del email",
        source_type="email",
        summary="Email origen: ejemplo",
    )

    assert repo.get_item(evernote_id)["summary"] == ""
    assert repo.get_item(email_id)["summary"] == ""


def test_manual_knowledge_summary_can_be_saved_by_user() -> None:
    repo = _repo()

    item_id = repo.create_item(
        title="Nota manual",
        content="Contenido",
        source_type="manual",
        summary="Resumen escrito manualmente",
    )

    assert repo.get_item(item_id)["summary"] == "Resumen escrito manualmente"


def test_update_item_summary_persists_summary_without_changing_content() -> None:
    repo = _repo()
    item_id = repo.create_item(title="Nota", content="Contenido original", source_type="manual")

    repo.update_item_summary(item_id, "Resumen IA bajo demanda")

    item = repo.get_item(item_id)
    assert item["summary"] == "Resumen IA bajo demanda"
    assert item["content"] == "Contenido original"
    assert "Resumen IA bajo demanda" in item["indexed_text"]


def test_knowledge_index_includes_note_metadata_and_search_combines_filters() -> None:
    repo = _repo()
    repo.create_topic("Tema A", area="General")
    matching_id = repo.create_item(
        title="Manual interno",
        content="El procedimiento contiene la palabra ultravioleta.",
        area="General",
        tipo="Procedimiento",
        tags=["operaciones"],
        source_type="manual",
        summary="Resumen manual",
    )
    repo.create_item(
        title="Otra nota",
        content="ultravioleta pero en otra área",
        area="Archivo",
        tipo="Nota",
    )

    indexed_text = repo.get_item(matching_id)["indexed_text"]

    assert "ultravioleta" in indexed_text
    assert "operaciones" in indexed_text
    assert [row["id"] for row in repo.list_items(search="ultravioleta", area="General", tipo="Procedimiento")] == [
        matching_id
    ]
    assert repo.list_items(search="ultravioleta", area="General", tipo="Nota") == []


def test_knowledge_index_includes_attachment_text_and_filename(tmp_path) -> None:
    repo = _repo()
    item_id = repo.create_item(title="Nota con texto adjunto", content="Contenido base")
    attachment_path = tmp_path / "contrato_busqueda.txt"
    attachment_path.write_text("Cláusula con palabra magnetar para búsqueda", encoding="utf-8")

    repo.add_attachment(
        item_id=item_id,
        original_filename="contrato_busqueda.txt",
        stored_filename="contrato_busqueda.txt",
        stored_path=str(attachment_path),
        mime_type="text/plain",
        file_size=attachment_path.stat().st_size,
    )

    assert [row["id"] for row in repo.list_items(search="magnetar")] == [item_id]
    assert [row["id"] for row in repo.list_items(search="contrato_busqueda")]


def test_knowledge_reindex_all_rebuilds_existing_empty_index(tmp_path) -> None:
    repo = _repo()
    item_id = repo.create_item(title="Nota antigua", content="Contenido sin índice")
    attachment_path = tmp_path / "archivo_antiguo.txt"
    attachment_path.write_text("Texto recuperado con palabra sincrotron", encoding="utf-8")
    repo.add_attachment(
        item_id=item_id,
        original_filename="archivo_antiguo.txt",
        stored_filename="archivo_antiguo.txt",
        stored_path=str(attachment_path),
        mime_type="text/plain",
        file_size=attachment_path.stat().st_size,
    )
    repo.update_indexed_text(item_id, "")

    assert repo.list_items(search="sincrotron") == []

    result = repo.reindex_all()

    assert result["ok"] >= 1
    assert [row["id"] for row in repo.list_items(search="sincrotron")] == [item_id]
