"""SQLite database management and migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return column in [row[1] for row in cursor.fetchall()]


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
        """
    )
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")


def obtener_version(conn: sqlite3.Connection) -> int:
    _ensure_schema_version_table(conn)
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else 0


def guardar_version(conn: sqlite3.Connection, version: int) -> None:
    _ensure_schema_version_table(conn)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def migracion_1(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pedidos'"
    ).fetchone()
    if table_exists is None:
        return
    columns = _table_columns(conn, "pedidos")
    if "fecha" not in columns:
        conn.execute("ALTER TABLE pedidos ADD COLUMN fecha TEXT")


def migracion_2(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pedidos'"
    ).fetchone()
    if table_exists is None:
        return
    if column_exists(conn, "pedidos", "NumeroPedido"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pedidos_numero
            ON pedidos (NumeroPedido)
            """
        )
        conn.commit()


def run_migrations(conn: sqlite3.Connection) -> None:
    current_version = obtener_version(conn)

    if current_version < 1:
        migracion_1(conn)
        guardar_version(conn, 1)

    if current_version < 2:
        migracion_2(conn)
        guardar_version(conn, 2)

    conn.commit()


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
                CREATE TABLE IF NOT EXISTS pedidos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    NumeroPedido TEXT,
                    Estado TEXT,
                    fecha DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Legacy databases might have been created without this column.
            self._ensure_column(conn, "pedidos", "fecha", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lineas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pedido_id INTEGER NOT NULL,
                    NumeroPedido TEXT,
                    linea INTEGER,
                    cantidad REAL,
                    cajas_totales REAL,
                    cp REAL,
                    tipo_palet TEXT,
                    nombre_caja TEXT,
                    mercancia TEXT,
                    confeccion TEXT,
                    calibre TEXT,
                    categoria TEXT,
                    marca TEXT,
                    po TEXT,
                    lote TEXT,
                    observaciones TEXT,
                    cliente TEXT,
                    comercial TEXT,
                    fecha_carga TEXT,
                    plataforma TEXT,
                    pais TEXT,
                    punto_carga TEXT,
                    estado TEXT,
                    archivo_origen TEXT
                )
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
            run_migrations(conn)
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
