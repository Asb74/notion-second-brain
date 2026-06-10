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
        "knowledge_tags",
        "knowledge_item_tags",
    }.issubset(tables)
    areas = [str(row[0]) for row in conn.execute("SELECT name FROM knowledge_areas ORDER BY sort_order ASC").fetchall()]
    assert areas[:5] == ["General", "Personal", "Trabajo", "Proyectos", "Archivo"]
    item_types = {
        str(row[0])
        for row in conn.execute("SELECT name FROM knowledge_item_types").fetchall()
    }
    assert {"Nota", "Email", "Audio", "Documento", "Imagen"}.issubset(item_types)
    indexes = {
        str(row[1])
        for row in conn.execute("PRAGMA index_list(knowledge_items)").fetchall()
    }
    assert {
        "idx_knowledge_items_area",
        "idx_knowledge_items_type",
        "idx_knowledge_items_source",
        "idx_knowledge_items_title",
    }.issubset(indexes)
