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
