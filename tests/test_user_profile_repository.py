import sqlite3

from app.persistence.db import Database
from app.persistence.user_profile_repository import UserProfileRepository


def test_user_profile_singleton_and_save(tmp_path) -> None:
    db = Database(tmp_path / "notes.db")
    db.migrate()
    conn = db.connect()
    repo = UserProfileRepository(conn)

    profile = repo.get_profile()
    assert profile["id"] == "1"

    repo.save_profile(
        nombre="Ana",
        cargo="PM",
        empresa="Acme",
        telefono="123",
        email="ana@acme.com",
        dominio_interno="acme.com",
    )

    saved = repo.get_profile()
    assert saved["nombre"] == "Ana"
    assert saved["cargo"] == "PM"
    assert saved["empresa"] == "Acme"
    assert saved["telefono"] == "123"
    assert saved["email"] == "ana@acme.com"
    assert saved["dominio_interno"] == "acme.com"

    rows = conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0]
    assert rows == 1


def test_migrate_managed_email_from_legacy_db(tmp_path) -> None:
    current_db = Database(tmp_path / "current" / "notes.db")
    current_db.migrate()
    conn = current_db.connect()
    repo = UserProfileRepository(conn)

    legacy_path = tmp_path / "legacy" / "notes.db"
    legacy_path.parent.mkdir()
    with sqlite3.connect(legacy_path) as legacy_conn:
        legacy_conn.execute(
            "CREATE TABLE user_profile (id INTEGER PRIMARY KEY, email TEXT NOT NULL DEFAULT '')"
        )
        legacy_conn.execute("INSERT INTO user_profile (id, email) VALUES (1, ?)", ("LEGACY@EXAMPLE.COM",))

    migrated = repo.migrate_managed_email_from_legacy(legacy_path)

    assert migrated == "legacy@example.com"
    assert repo.get_email() == "legacy@example.com"


def test_migrate_managed_email_from_legacy_settings_fallback(tmp_path) -> None:
    current_db = Database(tmp_path / "current" / "notes.db")
    current_db.migrate()
    conn = current_db.connect()
    repo = UserProfileRepository(conn)

    legacy_path = tmp_path / "legacy" / "notes.db"
    legacy_path.parent.mkdir()
    with sqlite3.connect(legacy_path) as legacy_conn:
        legacy_conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        legacy_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('managed_email', ?)",
            ("settings@example.com",),
        )

    migrated = repo.migrate_managed_email_from_legacy(legacy_path)

    assert migrated == "settings@example.com"
    assert repo.get_email() == "settings@example.com"
