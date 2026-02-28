"""SQLite database management and migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


class Database:
    """Simple SQLite wrapper with schema migrations."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Create a connection with row factory enabled."""
        conn = sqlite3.connect(
            database=str(self.db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self) -> None:
        """Apply schema migrations (idempotent)."""
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notes_local (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    area TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    prioridad TEXT NOT NULL,
                    fecha TEXT NOT NULL,
                    resumen TEXT NOT NULL DEFAULT '',
                    acciones TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    notion_page_id TEXT,
                    last_error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT
                )
                """
            )
            self._ensure_column(conn, "notes_local", "resumen", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "notes_local", "acciones", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    area TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pendiente',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_actions_status_area
                ON actions(status, area)
                """
            )
            self._migrate_masters_table(conn)
            conn.commit()

    def _migrate_masters_table(self, conn: sqlite3.Connection) -> None:
        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "masters" not in existing_tables:
            conn.execute(
                """
                CREATE TABLE masters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    value TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    system_locked INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(category, value)
                )
                """
            )
            return

        columns = _table_columns(conn, "masters")
        expected = {"id", "category", "value", "active", "system_locked"}
        if expected.issubset(columns):
            return

        conn.execute(
            """
            CREATE TABLE masters_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                system_locked INTEGER NOT NULL DEFAULT 0,
                UNIQUE(category, value)
            )
            """
        )

        if {"field_name", "is_active", "value"}.issubset(columns):
            conn.execute(
                """
                INSERT INTO masters_new(category, value, active, system_locked)
                SELECT field_name, value, is_active, 0
                FROM masters
                """
            )

        conn.execute("DROP TABLE masters")
        conn.execute("ALTER TABLE masters_new RENAME TO masters")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_spec: str) -> None:
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {column[1] for column in columns}
        if column_name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_spec}")

    def get_setting(self, key: str) -> str | None:
        """Return a setting value from settings table or None if missing."""
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        """Insert or update one setting in settings table."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()


def default_data_dir() -> Path:
    """Return default AppData directory for user data on Windows-compatible layout."""
    appdata = Path.home() / "AppData" / "Roaming"
    return appdata / "NotionSecondBrain"
