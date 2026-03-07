"""Repository for Google calendars persisted in SQLite."""

from __future__ import annotations

import sqlite3
from datetime import datetime


class CalendarRepository:
    """Data access layer for calendars table."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_calendar(
        self,
        google_calendar_id: str,
        name: str,
        background_color: str,
        foreground_color: str,
        is_primary: int,
        access_role: str,
        selected: int,
        updated_at: str,
    ) -> None:
        final_updated_at = updated_at or datetime.utcnow().isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO calendars (
                google_calendar_id,
                name,
                background_color,
                foreground_color,
                is_primary,
                access_role,
                selected,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(google_calendar_id) DO UPDATE SET
                name = excluded.name,
                background_color = excluded.background_color,
                foreground_color = excluded.foreground_color,
                is_primary = excluded.is_primary,
                access_role = excluded.access_role,
                selected = excluded.selected,
                updated_at = excluded.updated_at
            """,
            (
                google_calendar_id,
                name,
                background_color,
                foreground_color,
                int(is_primary),
                access_role,
                int(selected),
                final_updated_at,
            ),
        )
        self.conn.commit()

    def list_calendars(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM calendars
            ORDER BY is_primary DESC, name COLLATE NOCASE ASC
            """
        ).fetchall()

    def list_selected_calendars(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM calendars
            WHERE selected = 1
            ORDER BY is_primary DESC, name COLLATE NOCASE ASC
            """
        ).fetchall()

    def get_calendar_by_google_id(self, google_calendar_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM calendars WHERE google_calendar_id = ?",
            (google_calendar_id,),
        ).fetchone()

    def get_primary_calendar(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM calendars
            WHERE is_primary = 1
            ORDER BY name COLLATE NOCASE ASC
            LIMIT 1
            """
        ).fetchone()

    def set_calendar_selected(self, google_calendar_id: str, selected: int) -> None:
        self.conn.execute(
            "UPDATE calendars SET selected = ?, updated_at = ? WHERE google_calendar_id = ?",
            (int(selected), datetime.utcnow().isoformat(timespec="seconds"), google_calendar_id),
        )
        self.conn.commit()

    def delete_missing_calendars(self, valid_google_ids: list[str]) -> None:
        if not valid_google_ids:
            self.conn.execute("DELETE FROM calendars")
            self.conn.commit()
            return

        placeholders = ",".join("?" for _ in valid_google_ids)
        self.conn.execute(
            f"DELETE FROM calendars WHERE google_calendar_id NOT IN ({placeholders})",
            tuple(valid_google_ids),
        )
        self.conn.commit()
