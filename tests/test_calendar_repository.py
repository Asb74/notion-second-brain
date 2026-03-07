from app.persistence.calendar_repository import CalendarRepository
from app.persistence.db import Database


def test_calendar_repository_upsert_and_selected(tmp_path):
    db = Database(tmp_path / "test.db")
    db.migrate()
    conn = db.connect()
    repo = CalendarRepository(conn)

    repo.upsert_calendar("primary", "Personal", "#111111", "#ffffff", 1, "owner", 1, "2024-01-01T00:00:00")
    repo.upsert_calendar("team", "Team", "#00ff00", "#000000", 0, "writer", 0, "2024-01-01T00:00:00")

    assert len(repo.list_calendars()) == 2
    assert len(repo.list_selected_calendars()) == 1
    assert repo.get_primary_calendar()["google_calendar_id"] == "primary"

    repo.set_calendar_selected("team", 1)
    assert len(repo.list_selected_calendars()) == 2

    repo.delete_missing_calendars(["team"])
    rows = repo.list_calendars()
    assert len(rows) == 1
    assert rows[0]["google_calendar_id"] == "team"
