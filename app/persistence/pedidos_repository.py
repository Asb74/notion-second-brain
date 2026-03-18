"""Repository for attachment-driven order lines with historical tracking."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class PedidosRepository:
    """Data access for persisted order lines extracted from attachments."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ensure_table()

    def ensure_table(self) -> None:
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
        pedidos = payload if isinstance(payload, list) else [payload]
        total_rows = 0

        for pedido in pedidos:
            if not isinstance(pedido, dict):
                continue
            pedido_id = str(pedido.get("PedidoID") or pedido.get("pedido_id") or "").strip()
            cliente = str(pedido.get("Cliente") or pedido.get("cliente") or "").strip()
            comercial = str(pedido.get("Comercial") or pedido.get("comercial") or "").strip()

            lineas = pedido.get("Lineas") or pedido.get("lineas") or []
            if isinstance(lineas, dict):
                lineas = [lineas]
            if not isinstance(lineas, list):
                continue

            for linea in lineas:
                if not isinstance(linea, dict):
                    continue
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
                        self._as_int(linea.get("Linea") or linea.get("linea")),
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
                        self._as_text(linea.get("Cliente") or cliente),
                        self._as_text(linea.get("Comercial") or comercial),
                        self._as_text(linea.get("FCarga") or linea.get("fecha_carga")),
                        self._as_text(linea.get("Plataforma") or linea.get("plataforma")),
                        self._as_text(linea.get("Pais") or linea.get("pais")),
                        self._as_text(linea.get("PCarga") or linea.get("punto_carga")),
                        self._as_text(linea.get("Estado") or linea.get("estado")),
                        False,
                        True,
                        (archivo_nombre or "").strip(),
                    ),
                )
                total_rows += 1

        self.conn.commit()
        return total_rows

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
        if value in (None, ""):
            return None
        try:
            return int(float(str(value).replace(",", ".").strip()))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_text(value: Any) -> str:
        return str(value or "").strip()
