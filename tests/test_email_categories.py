import sqlite3

import pytest

from app.core.email.category_manager import CategoryManager
from app.persistence.email_repository import EmailRepository


def _repo() -> EmailRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return EmailRepository(conn)


def test_base_categories_seeded() -> None:
    repo = _repo()

    names = repo.get_category_names()

    assert set(names) == {"priority", "order", "subscription", "marketing", "other"}


def test_dynamic_categories_limit() -> None:
    manager = CategoryManager(_repo())

    for i in range(1, 6):
        manager.create_category(f"Nueva {i}")

    with pytest.raises(ValueError, match="Máximo 5 categorías adicionales permitidas"):
        manager.create_category("Extra")


def test_delete_category_cleans_labels() -> None:
    repo = _repo()
    manager = CategoryManager(repo)
    created = manager.create_category("Clientes VIP")

    repo.conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, received_at, body_text, status, category, type)
        VALUES ('g1', 'Hola', 'a@sansebas.es', '2024-01-01T00:00:00+00:00', 'body', 'new', 'pending', ?)
        """,
        (created["name"],),
    )
    repo.conn.commit()
    repo.save_label("g1", created["name"], source="user")

    manager.delete_category(created["name"])

    label_row = repo.conn.execute("SELECT * FROM email_labels WHERE gmail_id = 'g1'").fetchone()
    email_row = repo.conn.execute("SELECT type FROM emails WHERE gmail_id = 'g1'").fetchone()
    assert label_row is None
    assert email_row["type"] == "other"


def test_mark_as_knowledge_persists_link_status_and_version() -> None:
    repo = _repo()
    repo.conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, received_at, body_text, status, category, type)
        VALUES ('g-knowledge', 'Tema', 'a@example.com', '2024-01-01T00:00:00+00:00', 'body', 'new', 'pending', 'other')
        """
    )
    repo.conn.commit()

    repo.mark_as_knowledge('g-knowledge', 42)
    row = repo.get_email_content('g-knowledge')

    assert row is not None
    assert row['status'] == 'Conocimiento'
    assert row['knowledge_note_id'] == 42
    assert row['knowledge_created_at']
    assert row['knowledge_version'] == 1


def test_unlink_knowledge_clears_link_and_knowledge_status() -> None:
    repo = _repo()
    repo.conn.execute(
        """
        INSERT INTO emails (
            gmail_id, subject, sender, received_at, body_text, status, category, type,
            knowledge_note_id, knowledge_created_at, knowledge_version
        ) VALUES (
            'g-unlink', 'Tema', 'a@example.com', '2024-01-01T00:00:00+00:00', 'body',
            'Conocimiento', 'pending', 'other', 42, '2024-01-01T00:00:00+00:00', 2
        )
        """
    )
    repo.conn.commit()

    repo.unlink_knowledge('g-unlink')
    row = repo.get_email_content('g-unlink')

    assert row is not None
    assert row['status'] == ''
    assert row['knowledge_note_id'] is None
    assert row['knowledge_created_at'] is None
    assert row['knowledge_version'] == 0
