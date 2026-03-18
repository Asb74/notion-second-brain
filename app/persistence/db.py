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
                    hora_inicio TEXT,
                    duracion INTEGER,
                    hora_fin TEXT,
                    resumen TEXT NOT NULL DEFAULT '',
                    acciones TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    notion_page_id TEXT,
                    last_error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    email_replied INTEGER NOT NULL DEFAULT 0,
                    google_event_id TEXT NOT NULL DEFAULT '',
                    google_calendar_link TEXT NOT NULL DEFAULT '',
                    google_calendar_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._ensure_column(conn, "notes_local", "resumen", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "notes_local", "acciones", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "notes_local", "hora_inicio", "TEXT")
            self._ensure_column(conn, "notes_local", "duracion", "INTEGER")
            self._ensure_column(conn, "notes_local", "hora_fin", "TEXT")
            self._ensure_column(conn, "notes_local", "email_replied", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "notes_local", "google_event_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "notes_local", "google_calendar_link", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "notes_local", "google_calendar_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calendars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    google_calendar_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    background_color TEXT NOT NULL DEFAULT '#9E9E9E',
                    foreground_color TEXT NOT NULL DEFAULT '#000000',
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    access_role TEXT NOT NULL DEFAULT '',
                    selected INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
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
                CREATE TABLE IF NOT EXISTS ml_training_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset TEXT NOT NULL,
                    input_text TEXT,
                    output_text TEXT,
                    label TEXT,
                    metadata TEXT,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS refinement_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset TEXT NOT NULL,
                    input_original TEXT NOT NULL,
                    output_original TEXT NOT NULL,
                    user_instruction TEXT NOT NULL,
                    refined_output TEXT NOT NULL,
                    refinement_mode TEXT NOT NULL DEFAULT 'email_summary',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                    completed_at TEXT,
                    notion_page_id TEXT
                )
                """
            )
            self._ensure_column(conn, "actions", "notion_page_id", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_actions_status_area
                ON actions(status, area)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pedidos_lineas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pedido_id TEXT,
                    linea INTEGER,
                    palets INTEGER,
                    nombre_palet TEXT,
                    total_cajas INTEGER,
                    cajas_palet INTEGER,
                    nombre_caja TEXT,
                    mercancia TEXT,
                    confeccion TEXT,
                    calibre TEXT,
                    categoria TEXT,
                    marca TEXT,
                    precio TEXT,
                    lote TEXT,
                    observaciones TEXT,
                    cliente TEXT,
                    comercial TEXT,
                    fecha_carga TEXT,
                    plataforma TEXT,
                    pais TEXT,
                    punto_carga TEXT,
                    estado TEXT,
                    leido BOOLEAN,
                    grabado BOOLEAN,
                    archivo_origen TEXT,
                    fecha_importacion DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pedidos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    PedidoID TEXT,
                    Linea INTEGER,
                    Cliente TEXT,
                    Mercancia TEXT,
                    Palets INTEGER,
                    Estado TEXT,
                    FechaProcesado DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_pedidos_pedido_linea
                ON pedidos(PedidoID, Linea)
                """
            )
            self._ensure_column(conn, "refinement_history", "refinement_mode", "TEXT NOT NULL DEFAULT 'email_summary'")
            self._migrate_masters_table(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    nombre TEXT NOT NULL DEFAULT '',
                    cargo TEXT NOT NULL DEFAULT '',
                    empresa TEXT NOT NULL DEFAULT '',
                    telefono TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    dominio_interno TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                INSERT INTO user_profile (id)
                VALUES (1)
                ON CONFLICT(id) DO NOTHING
                """
            )
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
