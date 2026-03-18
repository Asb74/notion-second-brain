import sqlite3

from app.persistence.pedidos_repository import PedidosRepository


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
                        {"Linea": 2, "Palets": 3, "Mercancia": "Naranja", "Estado": "Cancelado"},
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
