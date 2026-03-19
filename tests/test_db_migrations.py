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
    assert obtener_version(conn) == 2
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

    assert obtener_version(conn) == 2


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

    assert obtener_version(conn) == 2
    idx = conn.execute("PRAGMA index_list(pedidos)").fetchall()
    assert not any(str(row[1]) == "idx_pedidos_numero" for row in idx)
