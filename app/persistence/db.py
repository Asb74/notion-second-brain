"""SQLite database management and migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
    cursor = conn.cursor()
    table_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pedidos'"
    ).fetchone()
    if table_exists is None:
        return

    cursor.execute("PRAGMA table_info(pedidos)")
    columnas = [row[1] for row in cursor.fetchall()]

    if "NumeroPedido" not in columnas:
        cursor.execute("ALTER TABLE pedidos ADD COLUMN NumeroPedido TEXT")

    if "Estado" not in columnas:
        cursor.execute("ALTER TABLE pedidos ADD COLUMN Estado TEXT")

    if "fecha" not in columnas:
        cursor.execute("ALTER TABLE pedidos ADD COLUMN fecha TEXT")

    conn.commit()


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


def migracion_3(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_training_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT,
            numero_pedido TEXT,
            source_file TEXT,
            pdf_text TEXT,
            extracted_json TEXT,
            corrected_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_order_training_unique
            ON order_training_examples(gmail_id, numero_pedido, source_file)
            """
        )
    except sqlite3.IntegrityError:
        # Legacy datasets may already contain duplicates; keep the table usable and
        # let repository-level conflict handling work when the unique index exists.
        pass
    conn.commit()



def ensure_knowledge_schema(conn: sqlite3.Connection) -> None:
    """Create Knowledge Manager tables, indexes, and compatibility migrations idempotently."""
    # Legacy Knowledge-specific masters. New code uses global masters for area/tipo.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            color TEXT,
            sort_order INTEGER DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_item_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            icon TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_id INTEGER,
            area TEXT,
            name TEXT NOT NULL,
            description TEXT,
            sort_order INTEGER DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(area_id) REFERENCES knowledge_areas(id),
            UNIQUE(area_id, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT,
            summary TEXT,
            area_id INTEGER,
            area TEXT,
            topic_id INTEGER,
            item_type_id INTEGER,
            tipo TEXT,
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT,
            source_path TEXT,
            indexed_text TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(area_id) REFERENCES knowledge_areas(id),
            FOREIGN KEY(topic_id) REFERENCES knowledge_topics(id),
            FOREIGN KEY(item_type_id) REFERENCES knowledge_item_types(id)
        )
        """
    )
    knowledge_item_columns = _table_columns(conn, "knowledge_items")
    if "topic_id" not in knowledge_item_columns:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN topic_id INTEGER")
    if "area" not in knowledge_item_columns:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN area TEXT")
    if "tipo" not in knowledge_item_columns:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN tipo TEXT")
    if "indexed_text" not in knowledge_item_columns:
        conn.execute("ALTER TABLE knowledge_items ADD COLUMN indexed_text TEXT")
    knowledge_topic_columns = _table_columns(conn, "knowledge_topics")
    if "area" not in knowledge_topic_columns:
        conn.execute("ALTER TABLE knowledge_topics ADD COLUMN area TEXT")
    if "description" not in knowledge_topic_columns:
        conn.execute("ALTER TABLE knowledge_topics ADD COLUMN description TEXT")

    conn.execute(
        """
        UPDATE knowledge_topics
        SET area = (
            SELECT ka.name
            FROM knowledge_areas ka
            WHERE ka.id = knowledge_topics.area_id
        )
        WHERE (area IS NULL OR TRIM(area) = '') AND area_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE knowledge_items
        SET area = (
            SELECT ka.name
            FROM knowledge_areas ka
            WHERE ka.id = knowledge_items.area_id
        )
        WHERE (area IS NULL OR TRIM(area) = '') AND area_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE knowledge_items
        SET tipo = (
            SELECT kit.name
            FROM knowledge_item_types kit
            WHERE kit.id = knowledge_items.item_type_id
        )
        WHERE (tipo IS NULL OR TRIM(tipo) = '') AND item_type_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_item_tags (
            item_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY(item_id, tag_id),
            FOREIGN KEY(item_id) REFERENCES knowledge_items(id),
            FOREIGN KEY(tag_id) REFERENCES knowledge_tags(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            mime_type TEXT,
            file_size INTEGER,
            source_type TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(item_id) REFERENCES knowledge_items(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_area ON knowledge_items(area_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_area_text ON knowledge_items(area)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_topic ON knowledge_items(topic_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_type ON knowledge_items(item_type_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_tipo_text ON knowledge_items(tipo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_topics_area ON knowledge_topics(area_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_topics_area_text ON knowledge_topics(area)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_source ON knowledge_items(source_type, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_title ON knowledge_items(title)")
    knowledge_attachment_columns = _table_columns(conn, "knowledge_attachments")
    if "ocr_text" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_text TEXT")
    if "ocr_text_raw" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_text_raw TEXT")
        conn.execute("UPDATE knowledge_attachments SET ocr_text_raw = ocr_text WHERE ocr_text_raw IS NULL AND ocr_text IS NOT NULL")
    if "ocr_text_corrected" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_text_corrected TEXT")
    if "ocr_text_ai" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_text_ai TEXT")
    if "ocr_quality_score" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_quality_score REAL")
    if "ocr_quality_reason" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_quality_reason TEXT")
    if "ocr_updated_at" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_updated_at TEXT")
    if "ocr_corrected_at" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_corrected_at TEXT")
    if "ocr_status" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_status TEXT")
    if "ocr_mode" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_mode TEXT")
    if "ocr_engine" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_engine TEXT")
    if "ocr_rotation" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_rotation INTEGER")
    if "ocr_characters" not in knowledge_attachment_columns:
        conn.execute("ALTER TABLE knowledge_attachments ADD COLUMN ocr_characters INTEGER")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_items_indexed_text ON knowledge_items(indexed_text)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_attachments_item ON knowledge_attachments(item_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entity_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            note_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            snippet TEXT,
            confidence REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(entity_id) REFERENCES knowledge_entities(id),
            FOREIGN KEY(note_id) REFERENCES knowledge_items(id)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_entities_unique
        ON knowledge_entities(entity_type, normalized_value)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_entity_links_unique
        ON knowledge_entity_links(entity_id, note_id, source)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entities_type ON knowledge_entities(entity_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entity_links_entity ON knowledge_entity_links(entity_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entity_links_note ON knowledge_entity_links(note_id)")

    conn.commit()


def migracion_4(conn: sqlite3.Connection) -> None:
    ensure_knowledge_schema(conn)


def run_migrations(conn: sqlite3.Connection) -> None:
    current_version = obtener_version(conn)

    if current_version < 1:
        migracion_1(conn)
        guardar_version(conn, 1)

    if current_version < 2:
        migracion_2(conn)
        guardar_version(conn, 2)

    if current_version < 3:
        migracion_3(conn)
        guardar_version(conn, 3)

    if current_version < 4:
        migracion_4(conn)
        guardar_version(conn, 4)

    # Keep Knowledge Manager schema idempotent even for databases whose schema_version
    # was advanced by older application builds.
    ensure_knowledge_schema(conn)

    cursor = conn.cursor()
    table_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pedidos'"
    ).fetchone()
    if table_exists is not None:
        cursor.execute("PRAGMA table_info(pedidos)")
        print("COLUMNAS PEDIDOS:", cursor.fetchall())

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
                    fecha TEXT
                )
                """
            )
            migracion_3(conn)
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
                    description TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    system_locked INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(category, value)
                )
                """
            )
            return

        columns = _table_columns(conn, "masters")
        if "description" not in columns and {"id", "category", "value", "active", "system_locked"}.issubset(columns):
            conn.execute("ALTER TABLE masters ADD COLUMN description TEXT")
            return

        expected = {"id", "category", "value", "description", "active", "system_locked"}
        if expected.issubset(columns):
            return

        conn.execute(
            """
            CREATE TABLE masters_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                description TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                system_locked INTEGER NOT NULL DEFAULT 0,
                UNIQUE(category, value)
            )
            """
        )

        if {"field_name", "is_active", "value"}.issubset(columns):
            conn.execute(
                """
                INSERT INTO masters_new(category, value, description, active, system_locked)
                SELECT field_name, value, NULL, is_active, 0
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
    from app.config.config_paths import app_data_dir

    return app_data_dir()
