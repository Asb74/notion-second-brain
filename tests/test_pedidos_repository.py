import sqlite3

from app.persistence.pedidos_repository import PedidosRepository, aplicar_estados


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_guardar_pedidos_desde_json_inserta_lineas() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)

    inserted = repo.guardar_pedidos_desde_json(
        {
            "Pedidos": [
                {
                    "NumeroPedido": "P-1",
                    "Cliente": "Cliente A",
                    "Comercial": "Comercial A",
                    "Lineas": [
                        {"Linea": 1, "Cantidad": 5, "Mercancia": "Naranja", "TipoPalet": "Euro.Retor", "CajasTotales": 150, "CP": 30},
                        {"Linea": 2, "Cantidad": 2, "Mercancia": "Limón", "TipoPalet": "Euro.Retor", "CajasTotales": 60, "CP": 30},
                    ],
                }
            ]
        },
        "pedido.pdf",
    )

    assert inserted == 2
    rows = conn.execute("SELECT NumeroPedido, linea, cliente, comercial, archivo_origen FROM lineas ORDER BY linea").fetchall()
    assert [dict(row) for row in rows] == [
        {
            "NumeroPedido": "P-1",
            "linea": 1,
            "cliente": "Cliente A",
            "comercial": "Comercial A",
            "archivo_origen": "pedido.pdf",
        },
        {
            "NumeroPedido": "P-1",
            "linea": 2,
            "cliente": "Cliente A",
            "comercial": "Comercial A",
            "archivo_origen": "pedido.pdf",
        },
    ]
    pedido = conn.execute("SELECT fecha FROM pedidos WHERE NumeroPedido = 'P-1' ORDER BY id LIMIT 1").fetchone()
    assert pedido is not None
    assert pedido["fecha"]


def test_ensure_table_agrega_columna_fecha_en_bases_legacy() -> None:
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
    PedidosRepository(conn)

    columnas = conn.execute("PRAGMA table_info(pedidos)").fetchall()
    nombres = {str(row[1]) for row in columnas}
    assert "fecha" in nombres


def test_obtener_resumen_palets_excluye_cancelados() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)

    repo.guardar_pedidos_desde_json(
        {
            "Pedidos": [
                {
                    "NumeroPedido": "P-1",
                    "Lineas": [
                        {"Linea": 1, "Cantidad": 5, "Mercancia": "Naranja", "TipoPalet": "Euro.Retor", "CajasTotales": 150, "CP": 30},
                        {"Linea": 2, "Cantidad": 3, "Mercancia": "Naranja", "TipoPalet": "Euro.Retor", "CajasTotales": 90, "CP": 30, "Observaciones": "CANCELADO"},
                    ],
                }
            ]
        },
        "pedido-1.pdf",
    )

    resumen = repo.obtener_resumen_palets()
    assert len(resumen) == 1
    assert resumen[0]["mercancia"] == "Naranja"
    assert resumen[0]["total_palets"] == 5


def test_guardar_pedidos_calcula_estado_nuevo_y_rectificado() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)

    inserted_1 = repo.guardar_pedidos_desde_json(
        [{"NumeroPedido": "P-2", "Linea": 1, "Mercancia": "Naranja", "Cantidad": 3, "TipoPalet": "Euro.Retor", "CajasTotales": 90, "CP": 30}],
        "pedido-2.pdf",
    )
    inserted_2 = repo.guardar_pedidos_desde_json(
        [{"NumeroPedido": "P-2", "Linea": 1, "Mercancia": "Naranja", "Cantidad": 4, "TipoPalet": "Euro.Retor", "CajasTotales": 120, "CP": 30}],
        "pedido-2b.pdf",
    )

    assert inserted_1 == 1
    assert inserted_2 == 1
    rows = conn.execute("SELECT estado FROM lineas WHERE NumeroPedido = 'P-2' ORDER BY id").fetchall()
    assert [row["estado"] for row in rows] == ["Nuevo", "Modificado"]


def test_guardar_pedidos_detecta_cancelado_desde_observaciones() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)

    inserted = repo.guardar_pedidos_desde_json(
        [{"NumeroPedido": "P-3", "Linea": 1, "Observaciones": "cliente CANCELADO por incidencia", "TipoPalet": "Euro.Retor", "Cantidad": 1, "CajasTotales": 30, "CP": 30}],
        "pedido-3.pdf",
    )

    assert inserted == 1
    row = conn.execute("SELECT estado FROM lineas WHERE NumeroPedido = 'P-3'").fetchone()
    assert row is not None
    assert row["estado"] == "Cancelado"


def test_aplicar_estados_modifica_lineas_en_memoria() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)
    repo.guardar_pedidos_desde_json([{"NumeroPedido": "P-4", "Linea": 7, "TipoPalet": "Euro.Retor", "Cantidad": 1, "CajasTotales": 30, "CP": 30}], "pedido-4.pdf")

    lineas = [{"NumeroPedido": "P-4", "Linea": 7, "TipoPalet": "Euro.Retor", "Cantidad": 1, "CajasTotales": 30, "CP": 30}, {"NumeroPedido": "P-5", "Linea": 1, "TipoPalet": "Euro.Retor", "Cantidad": 1, "CajasTotales": 30, "CP": 30}]
    resultado = aplicar_estados(conn, lineas)

    assert resultado[0]["Estado"] == "Sin cambios"
    assert resultado[1]["Estado"] == "Nuevo"
