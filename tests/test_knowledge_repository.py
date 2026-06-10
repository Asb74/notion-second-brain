import sqlite3

from app.persistence.db import run_migrations
from app.persistence.knowledge_repository import KnowledgeRepository


def _repo() -> KnowledgeRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    return KnowledgeRepository(conn)


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
