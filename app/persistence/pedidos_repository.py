"""Repository for attachment-driven order lines with historical tracking."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
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
        linea.get("TipoPalet", ""),
    ]

    for campo in campos:
        if "CANCEL" in normalizar_texto(campo):
            return True

    return False


def _to_comp_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        num = float(str(value).strip())
        if num.is_integer():
            return str(int(num))
        return str(num)
    except (TypeError, ValueError):
        return str(value).strip()


def _linea_para_comparacion(linea: dict[str, Any]) -> dict[str, str]:
    return {
        "Linea": _to_comp_text(linea.get("Linea")),
        "Cantidad": _to_comp_text(linea.get("Cantidad") or linea.get("Palets")),
        "CajasTotales": _to_comp_text(linea.get("CajasTotales") or linea.get("TCajas")),
        "CP": _to_comp_text(linea.get("CP")),
        "TipoPalet": str(linea.get("TipoPalet") or linea.get("NombrePalet") or "").strip(),
        "NombreCaja": str(linea.get("NombreCaja") or "").strip(),
        "Mercancia": str(linea.get("Mercancia") or "").strip(),
        "Confeccion": str(linea.get("Confeccion") or "").strip(),
        "Calibre": str(linea.get("Calibre") or "").strip(),
        "Categoria": str(linea.get("Categoria") or "").strip(),
        "Marca": str(linea.get("Marca") or "").strip(),
        "PO": str(linea.get("PO") or "").strip(),
        "Lote": str(linea.get("Lote") or "").strip(),
        "Observaciones": str(linea.get("Observaciones") or "").strip(),
    }


def _obtener_lineas_ultima_version(db: sqlite3.Connection, numero_pedido: str) -> list[dict[str, Any]]:
    query = """
    SELECT linea, cantidad, cajas_totales, cp, tipo_palet, nombre_caja, mercancia, confeccion, calibre, categoria, marca, po, lote, observaciones
    FROM lineas
    WHERE pedido_id = (
        SELECT id FROM pedidos
        WHERE numero_pedido = ?
        ORDER BY fecha DESC, id DESC
        LIMIT 1
    )
    ORDER BY linea
    """
    rows = db.execute(query, (numero_pedido,)).fetchall()
    return [
        {
            "Linea": row["linea"],
            "Cantidad": row["cantidad"],
            "CajasTotales": row["cajas_totales"],
            "CP": row["cp"],
            "TipoPalet": row["tipo_palet"],
            "NombreCaja": row["nombre_caja"],
            "Mercancia": row["mercancia"],
            "Confeccion": row["confeccion"],
            "Calibre": row["calibre"],
            "Categoria": row["categoria"],
            "Marca": row["marca"],
            "PO": row["po"],
            "Lote": row["lote"],
            "Observaciones": row["observaciones"],
        }
        for row in rows
    ]


def calcular_estado_pedido(pedido_nuevo: dict[str, Any], pedido_existente: dict[str, Any] | None) -> str:
    if pedido_nuevo.get("cancelado", False):
        return "Cancelado"
    if pedido_existente is None:
        return "Nuevo"
    if pedido_nuevo.get("Lineas", []) != pedido_existente.get("Lineas", []):
        return "Modificado"
    return "Sin cambios"


def aplicar_estados(db: sqlite3.Connection, lineas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for linea in lineas:
        numero_pedido = str(linea.get("NumeroPedido") or linea.get("PedidoID") or "").strip()
        existentes = _obtener_lineas_ultima_version(db, numero_pedido) if numero_pedido else []
        linea["Estado"] = calcular_estado_pedido(
            {"Lineas": [_linea_para_comparacion(linea)], "cancelado": detectar_cancelado(linea)},
            {"Lineas": [_linea_para_comparacion(item) for item in existentes]} if existentes else None,
        )

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
                numero_pedido TEXT,
                estado TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columnas_pedidos = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(pedidos)").fetchall()
        }
        if "fecha" not in columnas_pedidos:
            self.conn.execute("ALTER TABLE pedidos ADD COLUMN fecha TEXT")
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pedidos_numero
            ON pedidos (numero_pedido)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lineas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL,
                numero_pedido TEXT,
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
        self.conn.commit()

    def guardar_pedidos_desde_json(self, data_json: str | dict[str, Any] | list[Any], archivo_nombre: str) -> int:
        payload = self._normalize_payload(data_json)
        total_rows = 0

        for pedido, linea in self._iter_line_items(payload):
            pedido_id = str(linea.get("NumeroPedido") or linea.get("PedidoID") or pedido.get("NumeroPedido") or pedido.get("PedidoID") or pedido.get("pedido_id") or "").strip()
            cliente = str(linea.get("Cliente") or pedido.get("Cliente") or pedido.get("cliente") or "").strip()
            comercial = str(linea.get("Comercial") or pedido.get("Comercial") or pedido.get("comercial") or "").strip()
            linea_numero = self._as_int(linea.get("Linea") or linea.get("linea"))
            lineas_existentes = _obtener_lineas_ultima_version(self.conn, pedido_id) if pedido_id else []
            estado_pedido = calcular_estado_pedido(
                {"Lineas": [_linea_para_comparacion(linea)], "cancelado": detectar_cancelado(linea)},
                {"Lineas": [_linea_para_comparacion(item) for item in lineas_existentes]} if lineas_existentes else None,
            )
            fecha = datetime.now().isoformat()
            pedido_row = self.conn.execute(
                """
                INSERT INTO pedidos (
                    numero_pedido, estado, fecha
                ) VALUES (?, ?, ?)
                """,
                (pedido_id, estado_pedido, fecha),
            )
            pedido_row_id = pedido_row.lastrowid
            linea["Estado"] = estado_pedido
            self.conn.execute(
                """
                INSERT INTO lineas (
                    pedido_id, numero_pedido, linea,
                    cantidad, cajas_totales, cp, tipo_palet,
                    nombre_caja, mercancia, confeccion, calibre, categoria, marca,
                    po, lote, observaciones,
                    cliente, comercial,
                    fecha_carga, plataforma, pais, punto_carga,
                    estado, archivo_origen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pedido_row_id,
                    pedido_id,
                    linea_numero,
                    self._as_int(linea.get("Cantidad") or linea.get("Palets") or linea.get("palets")),
                    self._as_int(linea.get("CajasTotales") or linea.get("TCajas") or linea.get("total_cajas")),
                    self._as_int(linea.get("CP") or linea.get("cajas_palet")),
                    self._as_text(linea.get("TipoPalet") or linea.get("NombrePalet") or linea.get("tipo_palet") or linea.get("nombre_palet")),
                    self._as_text(linea.get("NombreCaja") or linea.get("nombre_caja")),
                    self._as_text(linea.get("Mercancia") or linea.get("mercancia")),
                    self._as_text(linea.get("Confeccion") or linea.get("confeccion")),
                    self._as_text(linea.get("Calibre") or linea.get("calibre")),
                    self._as_text(linea.get("Categoria") or linea.get("categoria")),
                    self._as_text(linea.get("Marca") or linea.get("marca")),
                    self._as_text(linea.get("PO") or linea.get("po") or linea.get("precio")),
                    self._as_text(linea.get("Lote") or linea.get("lote")),
                    self._as_text(linea.get("Observaciones") or linea.get("observaciones")),
                    self._as_text(cliente),
                    self._as_text(comercial),
                    self._as_text(linea.get("FCarga") or linea.get("fecha_carga")),
                    self._as_text(linea.get("Plataforma") or linea.get("plataforma")),
                    self._as_text(linea.get("Pais") or linea.get("pais")),
                    self._as_text(linea.get("PCarga") or linea.get("punto_carga")),
                    estado_pedido,
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
            if raw_lines is None and any(key in pedido for key in ("Linea", "linea", "Mercancia", "PedidoID", "NumeroPedido")):
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
            SELECT l.*
            FROM lineas l
            JOIN pedidos p ON p.id = l.pedido_id
            WHERE l.pedido_id IN (
                SELECT MAX(id)
                FROM pedidos
                GROUP BY numero_pedido
            )
            ORDER BY numero_pedido, linea
            """
        ).fetchall()

    def obtener_lineas_ultima_version_por_pedido(self, numero_pedido: str) -> list[dict[str, str]]:
        return _obtener_lineas_ultima_version(self.conn, str(numero_pedido or "").strip())

    def obtener_resumen_palets(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT mercancia, SUM(cantidad) AS total_palets
            FROM lineas
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
