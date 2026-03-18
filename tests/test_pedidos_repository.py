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
                    "PedidoID": "P-1",
                    "Cliente": "Cliente A",
                    "Comercial": "Comercial A",
                    "Lineas": [
                        {"Linea": 1, "Palets": 5, "Mercancia": "Naranja", "Estado": "Nuevo"},
                        {"Linea": 2, "Palets": 2, "Mercancia": "Limón", "Estado": "Cancelado"},
                    ],
                }
            ]
        },
        "pedido.pdf",
    )

    assert inserted == 2
    rows = conn.execute("SELECT pedido_id, linea, cliente, comercial, archivo_origen FROM pedidos_lineas ORDER BY linea").fetchall()
    assert [dict(row) for row in rows] == [
        {
            "pedido_id": "P-1",
            "linea": 1,
            "cliente": "Cliente A",
            "comercial": "Comercial A",
            "archivo_origen": "pedido.pdf",
        },
        {
            "pedido_id": "P-1",
            "linea": 2,
            "cliente": "Cliente A",
            "comercial": "Comercial A",
            "archivo_origen": "pedido.pdf",
        },
    ]


def test_obtener_resumen_palets_excluye_cancelados() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)

    repo.guardar_pedidos_desde_json(
        {
            "Pedidos": [
                {
                    "PedidoID": "P-1",
                    "Lineas": [
                        {"Linea": 1, "Palets": 5, "Mercancia": "Naranja", "Estado": "Nuevo"},
                        {"Linea": 2, "Palets": 3, "Mercancia": "Naranja", "Observaciones": "CANCELADO"},
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
        [{"PedidoID": "P-2", "Linea": 1, "Mercancia": "Naranja", "Palets": 3}],
        "pedido-2.pdf",
    )
    inserted_2 = repo.guardar_pedidos_desde_json(
        [{"PedidoID": "P-2", "Linea": 1, "Mercancia": "Naranja", "Palets": 4}],
        "pedido-2b.pdf",
    )

    assert inserted_1 == 1
    assert inserted_2 == 1
    rows = conn.execute("SELECT estado FROM pedidos_lineas WHERE pedido_id = 'P-2' ORDER BY id").fetchall()
    assert [row["estado"] for row in rows] == ["Nuevo", "Rectificado"]


def test_guardar_pedidos_detecta_cancelado_desde_observaciones() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)

    inserted = repo.guardar_pedidos_desde_json(
        [{"PedidoID": "P-3", "Linea": 1, "Observaciones": "cliente CANCELADO por incidencia"}],
        "pedido-3.pdf",
    )

    assert inserted == 1
    row = conn.execute("SELECT estado FROM pedidos_lineas WHERE pedido_id = 'P-3'").fetchone()
    assert row is not None
    assert row["estado"] == "Cancelado"


def test_aplicar_estados_modifica_lineas_en_memoria() -> None:
    conn = _conn()
    repo = PedidosRepository(conn)
    repo.guardar_pedidos_desde_json([{"PedidoID": "P-4", "Linea": 7}], "pedido-4.pdf")

    lineas = [{"PedidoID": "P-4", "Linea": 7}, {"PedidoID": "P-5", "Linea": 1}]
    resultado = aplicar_estados(conn, lineas)

    assert resultado[0]["Estado"] == "Rectificado"
    assert resultado[1]["Estado"] == "Nuevo"
