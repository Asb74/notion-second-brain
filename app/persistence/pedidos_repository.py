"""Repository for attachment-driven order lines with historical tracking."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def normalizar_texto(texto: Any) -> str:
    if not texto:
        return ""
    return str(texto).upper().strip()


def detectar_cancelado(linea: dict[str, Any]) -> bool:
    campos = [
        linea.get("Observaciones", ""),
        linea.get("Mercancia", ""),
        linea.get("NombreCaja", ""),
    ]

    for campo in campos:
        if "CANCEL" in normalizar_texto(campo):
            return True

    return False


def existe_linea_en_bd(db: sqlite3.Connection, linea: dict[str, Any]) -> bool:
    query = """
    SELECT 1 FROM pedidos
    WHERE PedidoID = ? AND Linea = ?
    LIMIT 1
    """
    result = db.execute(query, (str(linea.get("PedidoID") or "").strip(), _safe_int(linea.get("Linea")))).fetchone()
    return result is not None


def calcular_estado_linea(db: sqlite3.Connection, linea: dict[str, Any]) -> str:
    if detectar_cancelado(linea):
        return "Cancelado"

    if existe_linea_en_bd(db, linea):
        return "Rectificado"

    return "Nuevo"


def aplicar_estados(db: sqlite3.Connection, lineas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for linea in lineas:
        linea["Estado"] = calcular_estado_linea(db, linea)

    return lineas


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except (TypeError, ValueError):
        return None


class PedidosRepository:
    """Data access for persisted order lines extracted from attachments."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ensure_table()

    def ensure_table(self) -> None:
        self.conn.execute(
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
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pedidos_pedido_linea
            ON pedidos(PedidoID, Linea)
            """
        )
        self.conn.execute(
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
        self.conn.commit()

    def guardar_pedidos_desde_json(self, data_json: str | dict[str, Any] | list[Any], archivo_nombre: str) -> int:
        payload = self._normalize_payload(data_json)
        total_rows = 0

        for pedido, linea in self._iter_line_items(payload):
            pedido_id = str(linea.get("PedidoID") or pedido.get("PedidoID") or pedido.get("pedido_id") or "").strip()
            cliente = str(linea.get("Cliente") or pedido.get("Cliente") or pedido.get("cliente") or "").strip()
            comercial = str(linea.get("Comercial") or pedido.get("Comercial") or pedido.get("comercial") or "").strip()
            linea_numero = self._as_int(linea.get("Linea") or linea.get("linea"))

            estado_linea = calcular_estado_linea(
                self.conn,
                {
                    "PedidoID": pedido_id,
                    "Linea": linea_numero,
                    "Observaciones": self._as_text(linea.get("Observaciones") or linea.get("observaciones")),
                    "Mercancia": self._as_text(linea.get("Mercancia") or linea.get("mercancia")),
                    "NombreCaja": self._as_text(linea.get("NombreCaja") or linea.get("nombre_caja")),
                },
            )
            linea["Estado"] = estado_linea
            self.conn.execute(
                """
                INSERT INTO pedidos (
                    PedidoID, Linea, Cliente, Mercancia, Palets, Estado
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(PedidoID, Linea) DO UPDATE SET
                    Cliente = excluded.Cliente,
                    Mercancia = excluded.Mercancia,
                    Palets = excluded.Palets,
                    Estado = excluded.Estado,
                    FechaProcesado = CURRENT_TIMESTAMP
                """,
                (
                    pedido_id,
                    linea_numero,
                    self._as_text(cliente),
                    self._as_text(linea.get("Mercancia") or linea.get("mercancia")),
                    self._as_int(linea.get("Palets") or linea.get("palets")),
                    estado_linea,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO pedidos_lineas (
                    pedido_id, linea,
                    palets, nombre_palet, total_cajas, cajas_palet,
                    nombre_caja, mercancia, confeccion, calibre, categoria, marca,
                    precio, lote, observaciones,
                    cliente, comercial,
                    fecha_carga, plataforma, pais, punto_carga,
                    estado, leido, grabado, archivo_origen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pedido_id,
                    linea_numero,
                    self._as_int(linea.get("Palets") or linea.get("palets")),
                    self._as_text(linea.get("NombrePalet") or linea.get("nombre_palet")),
                    self._as_int(linea.get("TCajas") or linea.get("total_cajas")),
                    self._as_int(linea.get("CP") or linea.get("cajas_palet")),
                    self._as_text(linea.get("NombreCaja") or linea.get("nombre_caja")),
                    self._as_text(linea.get("Mercancia") or linea.get("mercancia")),
                    self._as_text(linea.get("Confeccion") or linea.get("confeccion")),
                    self._as_text(linea.get("Calibre") or linea.get("calibre")),
                    self._as_text(linea.get("Categoria") or linea.get("categoria")),
                    self._as_text(linea.get("Marca") or linea.get("marca")),
                    self._as_text(linea.get("PO") or linea.get("precio")),
                    self._as_text(linea.get("Lote") or linea.get("lote")),
                    self._as_text(linea.get("Observaciones") or linea.get("observaciones")),
                    self._as_text(cliente),
                    self._as_text(comercial),
                    self._as_text(linea.get("FCarga") or linea.get("fecha_carga")),
                    self._as_text(linea.get("Plataforma") or linea.get("plataforma")),
                    self._as_text(linea.get("Pais") or linea.get("pais")),
                    self._as_text(linea.get("PCarga") or linea.get("punto_carga")),
                    estado_linea,
                    False,
                    True,
                    (archivo_nombre or "").strip(),
                ),
            )
            total_rows += 1

        self.conn.commit()
        return total_rows

    @staticmethod
    def _iter_line_items(payload: dict[str, Any] | list[Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        pedidos = payload if isinstance(payload, list) else [payload]
        result: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for pedido in pedidos:
            if not isinstance(pedido, dict):
                continue

            raw_lines = pedido.get("Lineas") or pedido.get("lineas")
            if raw_lines is None and any(key in pedido for key in ("Linea", "linea", "Mercancia", "PedidoID")):
                raw_lines = [pedido]
            if isinstance(raw_lines, dict):
                raw_lines = [raw_lines]
            if not isinstance(raw_lines, list):
                continue

            for linea in raw_lines:
                if not isinstance(linea, dict):
                    continue
                result.append((pedido, linea))

        return result

    def obtener_ultima_version_lineas(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM pedidos_lineas pl
            WHERE fecha_importacion = (
                SELECT MAX(fecha_importacion)
                FROM pedidos_lineas
                WHERE pedido_id = pl.pedido_id
                  AND linea = pl.linea
            )
            ORDER BY pedido_id, linea
            """
        ).fetchall()

    def obtener_resumen_palets(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT mercancia, SUM(palets) AS total_palets
            FROM pedidos_lineas
            WHERE estado != 'Cancelado'
            GROUP BY mercancia
            ORDER BY mercancia
            """
        ).fetchall()

    @staticmethod
    def _normalize_payload(data_json: str | dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
        if isinstance(data_json, str):
            payload = json.loads(data_json)
        else:
            payload = data_json

        if isinstance(payload, dict):
            for key in ("Pedidos", "pedidos", "orders"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    return nested
            return payload
        if isinstance(payload, list):
            return payload
        raise ValueError("Formato de pedido no soportado")

    @staticmethod
    def _as_int(value: Any) -> int | None:
        return _safe_int(value)

    @staticmethod
    def _as_text(value: Any) -> str:
        return str(value or "").strip()
