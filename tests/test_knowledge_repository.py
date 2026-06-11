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
