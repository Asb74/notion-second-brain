"""Repository for attachment-driven order lines with historical tracking."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

COLUMN_NUMERO_PEDIDO = "NumeroPedido"
COLUMN_ESTADO = "Estado"
COLUMN_FECHA = "fecha"
MAPEO_DB = {
    "NumeroPedido": "numero_pedido",
    "Cliente": "cliente",
    "Comercial": "comercial",
    "FechaSalida": "fecha_salida",
    "Plataforma": "plataforma",
    "Pais": "pais",
    "PuntoCarga": "punto_carga",
    "Estado": "estado",
    "Linea": "linea",
    "Cantidad": "cantidad",
    "TipoPalet": "tipo_palet",
    "CajasTotales": "cajas_totales",
    "NombreCaja": "nombre_caja",
    "Mercancia": "mercancia",
    "Confeccion": "confeccion",
    "Calibre": "calibre",
    "Categoria": "categoria",
    "Marca": "marca",
    "PO": "po",
    "Lote": "lote",
    "Observaciones": "observaciones",
    "CP": "cp",
}


def mapear_a_db(linea: dict[str, Any]) -> dict[str, Any]:
    return {MAPEO_DB.get(k, k): v for k, v in linea.items()}


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


def _obtener_lineas_ultima_version(db: sqlite3.Connection, NumeroPedido: str) -> list[dict[str, Any]]:
    query = f"""
    SELECT
        linea, cantidad, cajas_totales, cp, tipo_palet, nombre_caja, mercancia, confeccion, calibre, categoria, marca, po, lote, observaciones,
        cliente, comercial, fecha_carga, plataforma, pais, punto_carga, estado
    FROM lineas
    WHERE pedido_id = (
        SELECT id FROM pedidos
        WHERE {COLUMN_NUMERO_PEDIDO} = ?
        ORDER BY {COLUMN_FECHA} DESC, id DESC
        LIMIT 1
    )
    ORDER BY linea
    """
    rows = db.execute(query, (NumeroPedido,)).fetchall()
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
            "Cliente": row["cliente"],
            "Comercial": row["comercial"],
            "FechaSalida": row["fecha_carga"],
            "Plataforma": row["plataforma"],
            "Pais": row["pais"],
            "PuntoCarga": row["punto_carga"],
            "Estado": row["estado"],
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
        NumeroPedido = str(linea.get("NumeroPedido") or linea.get("PedidoID") or "").strip()
        existentes = _obtener_lineas_ultima_version(db, NumeroPedido) if NumeroPedido else []
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
        self._lineas_numero_pedido_col = self._resolve_lineas_numero_pedido_column()

    def ensure_table(self) -> None:
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {COLUMN_NUMERO_PEDIDO} TEXT,
                {COLUMN_ESTADO} TEXT,
                {COLUMN_FECHA} TEXT
            )
            """
        )
        columnas_pedidos = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(pedidos)").fetchall()
        }
        if COLUMN_NUMERO_PEDIDO not in columnas_pedidos:
            self.conn.execute(f"ALTER TABLE pedidos ADD COLUMN {COLUMN_NUMERO_PEDIDO} TEXT")
        if COLUMN_ESTADO not in columnas_pedidos:
            self.conn.execute(f"ALTER TABLE pedidos ADD COLUMN {COLUMN_ESTADO} TEXT")
        if COLUMN_FECHA not in columnas_pedidos:
            self.conn.execute(f"ALTER TABLE pedidos ADD COLUMN {COLUMN_FECHA} TEXT")
        self.conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_pedidos_numero
            ON pedidos ({COLUMN_NUMERO_PEDIDO})
            """
        )
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS lineas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL,
                {COLUMN_NUMERO_PEDIDO} TEXT,
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
            linea_db = mapear_a_db(linea)
            pedido_db = mapear_a_db(pedido)
            pedido_id = str(
                linea.get("NumeroPedido")
                or linea_db.get("numero_pedido")
                or linea.get("PedidoID")
                or pedido.get("NumeroPedido")
                or pedido_db.get("numero_pedido")
                or pedido.get("PedidoID")
                or pedido.get("pedido_id")
                or ""
            ).strip()
            cliente = str(linea.get("Cliente") or linea_db.get("cliente") or pedido.get("Cliente") or pedido_db.get("cliente") or pedido.get("cliente") or "").strip()
            comercial = str(
                linea.get("Comercial") or linea_db.get("comercial") or pedido.get("Comercial") or pedido_db.get("comercial") or pedido.get("comercial") or ""
            ).strip()
            linea_numero = self._as_int(linea.get("Linea") or linea_db.get("linea") or linea.get("linea"))
            lineas_existentes = _obtener_lineas_ultima_version(self.conn, pedido_id) if pedido_id else []
            estado_pedido = calcular_estado_pedido(
                {"Lineas": [_linea_para_comparacion(linea)], "cancelado": detectar_cancelado(linea)},
                {"Lineas": [_linea_para_comparacion(item) for item in lineas_existentes]} if lineas_existentes else None,
            )
            fecha = datetime.now().isoformat()
            pedido_row = self.conn.execute(
                f"""
                INSERT INTO pedidos (
                    {COLUMN_NUMERO_PEDIDO}, {COLUMN_ESTADO}, {COLUMN_FECHA}
                ) VALUES (?, ?, ?)
                """,
                (pedido_id, estado_pedido, fecha),
            )
            pedido_row_id = pedido_row.lastrowid
            linea["Estado"] = estado_pedido
            self.conn.execute(
                f"""
                INSERT INTO lineas (
                    pedido_id, {self._lineas_numero_pedido_col}, linea,
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
                    self._as_int(linea.get("Cantidad") or linea_db.get("cantidad") or linea.get("Palets") or linea.get("palets")),
                    self._as_int(linea.get("CajasTotales") or linea_db.get("cajas_totales") or linea.get("TCajas") or linea.get("total_cajas")),
                    self._as_int(linea.get("CP") or linea_db.get("cp") or linea.get("cajas_palet")),
                    self._as_text(linea.get("TipoPalet") or linea_db.get("tipo_palet") or linea.get("NombrePalet") or linea.get("tipo_palet") or linea.get("nombre_palet")),
                    self._as_text(linea.get("NombreCaja") or linea_db.get("nombre_caja") or linea.get("nombre_caja")),
                    self._as_text(linea.get("Mercancia") or linea_db.get("mercancia") or linea.get("mercancia")),
                    self._as_text(linea.get("Confeccion") or linea_db.get("confeccion") or linea.get("confeccion")),
                    self._as_text(linea.get("Calibre") or linea_db.get("calibre") or linea.get("calibre")),
                    self._as_text(linea.get("Categoria") or linea_db.get("categoria") or linea.get("categoria")),
                    self._as_text(linea.get("Marca") or linea_db.get("marca") or linea.get("marca")),
                    self._as_text(linea.get("PO") or linea_db.get("po") or linea.get("po") or linea.get("precio")),
                    self._as_text(linea.get("Lote") or linea_db.get("lote") or linea.get("lote")),
                    self._as_text(linea.get("Observaciones") or linea_db.get("observaciones") or linea.get("observaciones")),
                    self._as_text(cliente),
                    self._as_text(comercial),
                    self._as_text(linea.get("FCarga") or linea.get("FechaSalida") or linea_db.get("fecha_carga") or linea_db.get("fecha_salida") or linea.get("fecha_carga")),
                    self._as_text(linea.get("Plataforma") or linea_db.get("plataforma") or linea.get("plataforma")),
                    self._as_text(linea.get("Pais") or linea_db.get("pais") or linea.get("pais")),
                    self._as_text(linea.get("PCarga") or linea.get("PuntoCarga") or linea_db.get("punto_carga") or linea.get("punto_carga")),
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
            f"""
            SELECT l.*
            FROM lineas l
            JOIN pedidos p ON p.id = l.pedido_id
            WHERE l.pedido_id IN (
                SELECT MAX(id)
                FROM pedidos
                GROUP BY {COLUMN_NUMERO_PEDIDO}
            )
            ORDER BY {COLUMN_NUMERO_PEDIDO}, linea
            """
        ).fetchall()

    def obtener_lineas_ultima_version_por_pedido(self, NumeroPedido: str) -> list[dict[str, str]]:
        return _obtener_lineas_ultima_version(self.conn, str(NumeroPedido or "").strip())

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

    def _resolve_lineas_numero_pedido_column(self) -> str:
        columnas_lineas = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(lineas)").fetchall()
        }
        if "numero_pedido" in columnas_lineas:
            return "numero_pedido"
        if COLUMN_NUMERO_PEDIDO in columnas_lineas:
            return COLUMN_NUMERO_PEDIDO
        return COLUMN_NUMERO_PEDIDO
