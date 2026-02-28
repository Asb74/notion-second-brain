"""Repositories for notes and settings."""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import datetime, timedelta
from typing import Optional

from app.core.models import Action, AppSettings, Note, NoteCreateRequest, NoteStatus


class NoteRepository:
    """Data access for notes table."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_note(self, req: NoteCreateRequest, source_id: str, created_at: str, status: NoteStatus) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO notes_local (
                created_at, source, source_id, title, raw_text, area, tipo, estado, prioridad, fecha, resumen, acciones, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                req.source,
                source_id,
                req.title,
                req.raw_text,
                req.area,
                req.tipo,
                req.estado,
                req.prioridad,
                req.fecha,
                req.resumen,
                req.acciones,
                status.value,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def source_exists(self, source_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM notes_local WHERE source_id = ?", (source_id,)).fetchone()
        return row is not None

    def list_notes(self, limit: int = 200) -> list[Note]:
        rows = self.conn.execute("SELECT * FROM notes_local ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_note(r) for r in rows]

    def get_note(self, note_id: int) -> Optional[Note]:
        row = self.conn.execute("SELECT * FROM notes_local WHERE id = ?", (note_id,)).fetchone()
        return self._to_note(row) if row else None

    def list_retryable(self, now_iso: str) -> list[Note]:
        rows = self.conn.execute(
            """
            SELECT * FROM notes_local
            WHERE status IN (?, ?)
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY id ASC
            """,
            (NoteStatus.PENDING.value, NoteStatus.ERROR.value, now_iso),
        ).fetchall()
        return [self._to_note(r) for r in rows]

    def mark_sent(self, note_id: int, notion_page_id: str) -> None:
        self.conn.execute(
            "UPDATE notes_local SET status = ?, notion_page_id = ?, last_error = NULL WHERE id = ?",
            (NoteStatus.SENT.value, notion_page_id, note_id),
        )
        self.conn.commit()

    def update_estado(self, note_id: int, estado: str) -> None:
        self.conn.execute(
            "UPDATE notes_local SET estado = ? WHERE id = ?",
            (estado, note_id),
        )
        self.conn.commit()

    def mark_error(self, note_id: int, error_msg: str, retry_after_seconds: int) -> None:
        attempts = self.conn.execute("SELECT attempts FROM notes_local WHERE id = ?", (note_id,)).fetchone()["attempts"]
        next_retry = datetime.utcnow() + timedelta(seconds=retry_after_seconds)
        self.conn.execute(
            """
            UPDATE notes_local
            SET status = ?, last_error = ?, attempts = ?, next_retry_at = ?
            WHERE id = ?
            """,
            (
                NoteStatus.ERROR.value,
                error_msg[:1000],
                attempts + 1,
                next_retry.isoformat(timespec="seconds"),
                note_id,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _to_note(row: sqlite3.Row) -> Note:
        return Note(**dict(row))


class SettingsRepository:
    """Persist and load app settings as key-value pairs."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_setting(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def load(self) -> AppSettings:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        values = {r["key"]: r["value"] for r in rows}
        base = AppSettings()
        for field_name in base.__dataclass_fields__.keys():
            if field_name in values:
                field_type = AppSettings.__dataclass_fields__[field_name].type
                raw_value = values[field_name]
                if field_type is int:
                    casted = int(raw_value)
                elif field_type is str:
                    casted = str(raw_value)
                else:
                    casted = raw_value
                setattr(base, field_name, casted)
        return base

    def save(self, settings: AppSettings) -> None:
        for f in fields(settings):
            key = f.name
            value = getattr(settings, key)
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
        self.conn.commit()


class ActionsRepository:
    """Data access for actions table."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_action(self, note_id: int, description: str, area: str) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO actions (note_id, description, area, status, created_at)
            VALUES (?, ?, ?, 'pendiente', ?)
            """,
            (note_id, description, area, datetime.utcnow().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def get_pending_actions(self) -> list[Action]:
        rows = self.conn.execute(
            """
            SELECT a.*
            FROM actions a
            WHERE a.status = 'pendiente'
            ORDER BY a.id DESC
            """
        ).fetchall()
        return [self._to_action(r) for r in rows]

    def get_actions_by_area(self, area: str) -> list[Action]:
        rows = self.conn.execute(
            """
            SELECT a.*
            FROM actions a
            WHERE a.area = ?
            ORDER BY a.id DESC
            """,
            (area,),
        ).fetchall()
        return [self._to_action(r) for r in rows]

    def get_action(self, action_id: int) -> Optional[Action]:
        row = self.conn.execute(
            """
            SELECT a.*
            FROM actions a
            WHERE a.id = ?
            """,
            (action_id,),
        ).fetchone()
        return self._to_action(row) if row else None

    def mark_action_done(self, action_id: int) -> None:
        self.conn.execute(
            """
            UPDATE actions
            SET status = 'hecha', completed_at = ?
            WHERE id = ?
            """,
            (datetime.utcnow().isoformat(timespec="seconds"), action_id),
        )
        self.conn.commit()


    def set_notion_page_id(self, action_id: int, notion_page_id: str) -> None:
        self.conn.execute(
            """
            UPDATE actions
            SET notion_page_id = ?
            WHERE id = ?
            """,
            (notion_page_id, action_id),
        )
        self.conn.commit()

    def pending_count_by_note(self, note_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(1) AS cnt FROM actions WHERE note_id = ? AND status != 'hecha'",
            (note_id,),
        ).fetchone()
        return int(row["cnt"]) if row else 0

    def get_actions_by_note(self, note_id: int) -> list[Action]:
        rows = self.conn.execute(
            """
            SELECT a.*
            FROM actions a
            WHERE a.note_id = ?
            ORDER BY a.id DESC
            """,
            (note_id,),
        ).fetchall()
        return [self._to_action(r) for r in rows]

    @staticmethod
    def _to_action(row: sqlite3.Row) -> Action:
        return Action(**dict(row))
