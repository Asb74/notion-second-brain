import sqlite3

from app.persistence.db import guardar_version, obtener_version, run_migrations


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_run_migrations_crea_schema_version_y_migra_pedidos() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            NumeroPedido TEXT,
            Estado TEXT
        )
        """
    )

    run_migrations(conn)

    columnas = conn.execute("PRAGMA table_info(pedidos)").fetchall()
    assert "fecha" in {str(row[1]) for row in columnas}
    assert obtener_version(conn) == 4
    idx = conn.execute("PRAGMA index_list(pedidos)").fetchall()
    assert any(str(row[1]) == "idx_pedidos_numero" for row in idx)


def test_run_migrations_es_idempotente() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            NumeroPedido TEXT,
            Estado TEXT
        )
        """
    )

    run_migrations(conn)
    guardar_version(conn, 1)
    run_migrations(conn)

    assert obtener_version(conn) == 4


def test_run_migrations_no_falla_si_no_existe_columna_NumeroPedido() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Estado TEXT
        )
        """
    )

    run_migrations(conn)

    assert obtener_version(conn) == 4
    columnas = conn.execute("PRAGMA table_info(pedidos)").fetchall()
    nombres = {str(row[1]) for row in columnas}
    assert "NumeroPedido" in nombres
    assert "Estado" in nombres
    assert "fecha" in nombres
    idx = conn.execute("PRAGMA index_list(pedidos)").fetchall()
    assert any(str(row[1]) == "idx_pedidos_numero" for row in idx)


def test_run_migrations_crea_tabla_order_training_examples() -> None:
    conn = _conn()

    run_migrations(conn)

    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_training_examples'"
    ).fetchone()
    assert table is not None
    columns = conn.execute("PRAGMA table_info(order_training_examples)").fetchall()
    names = {str(row[1]) for row in columns}
    assert {
        "id",
        "gmail_id",
        "numero_pedido",
        "source_file",
        "pdf_text",
        "extracted_json",
        "corrected_json",
        "status",
        "notes",
        "created_at",
        "updated_at",
    }.issubset(names)
    indexes = conn.execute("PRAGMA index_list(order_training_examples)").fetchall()
    assert any(str(row[1]) == "idx_order_training_unique" for row in indexes)


def test_run_migrations_crea_schema_knowledge_manager() -> None:
    conn = _conn()

    run_migrations(conn)
    run_migrations(conn)

    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "knowledge_areas",
        "knowledge_item_types",
        "knowledge_items",
        "knowledge_topics",
        "knowledge_tags",
        "knowledge_item_tags",
    }.issubset(tables)
    assert conn.execute("SELECT COUNT(*) FROM knowledge_areas").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM knowledge_item_types").fetchone()[0] == 0
    item_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()
    }
    assert {"topic_id", "area", "tipo"}.issubset(item_columns)
    topic_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(knowledge_topics)").fetchall()
    }
    assert "area" in topic_columns
    indexes = {
        str(row[1])
        for row in conn.execute("PRAGMA index_list(knowledge_items)").fetchall()
    }
    assert {
        "idx_knowledge_items_area",
        "idx_knowledge_items_area_text",
        "idx_knowledge_items_topic",
        "idx_knowledge_items_type",
        "idx_knowledge_items_tipo_text",
        "idx_knowledge_items_source",
        "idx_knowledge_items_title",
    }.issubset(indexes)
    topic_indexes = {
        str(row[1])
        for row in conn.execute("PRAGMA index_list(knowledge_topics)").fetchall()
    }
    assert {"idx_knowledge_topics_area", "idx_knowledge_topics_area_text"}.issubset(topic_indexes)


def test_run_migrations_backfills_knowledge_area_tipo_text_from_legacy_ids() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version VALUES (4);
        CREATE TABLE knowledge_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            color TEXT,
            sort_order INTEGER DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE knowledge_item_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            icon TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE knowledge_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            sort_order INTEGER DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(area_id) REFERENCES knowledge_areas(id),
            UNIQUE(area_id, name)
        );
        CREATE TABLE knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT,
            summary TEXT,
            area_id INTEGER,
            topic_id INTEGER,
            item_type_id INTEGER,
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT,
            source_path TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(area_id) REFERENCES knowledge_areas(id),
            FOREIGN KEY(topic_id) REFERENCES knowledge_topics(id),
            FOREIGN KEY(item_type_id) REFERENCES knowledge_item_types(id)
        );
        INSERT INTO knowledge_areas(id, name, created_at) VALUES (1, 'Legacy Area', 'now');
        INSERT INTO knowledge_item_types(id, name, created_at) VALUES (1, 'Legacy Type', 'now');
        INSERT INTO knowledge_topics(id, area_id, name, created_at) VALUES (1, 1, 'Legacy Topic', 'now');
        INSERT INTO knowledge_items(id, title, area_id, item_type_id, topic_id, source_type, status, created_at)
        VALUES (1, 'Legacy Item', 1, 1, 1, 'manual', 'active', 'now');
        """
    )

    run_migrations(conn)

    item = conn.execute("SELECT area, tipo FROM knowledge_items WHERE id = 1").fetchone()
    topic = conn.execute("SELECT area FROM knowledge_topics WHERE id = 1").fetchone()
    assert item["area"] == "Legacy Area"
    assert item["tipo"] == "Legacy Type"
    assert topic["area"] == "Legacy Area"
