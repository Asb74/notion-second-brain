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
