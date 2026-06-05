"""Extractor tolerante para pedidos tipo Anecoop.

El parser evita catálogos cerrados: no valida clientes, productos, marcas,
confecciones ni tipos de palet contra listas fijas. Extrae por etiquetas,
posición en el bloque y patrones generales de formato.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_LINE_STOP_RE = re.compile(r"(?i)^(?:total\b|observa\w*\b)$")
_LABEL_RE = re.compile(
    r"(?i)^(?:calibre|cal\.?|cat\.?|ct\.?|categor[ií]a|marca|lote|po|observa\w*|total\s+(?:cajas|ud\.?\s*venta))\s*:"
)
_PACKAGING_RE = re.compile(
    r"(?ix)"
    r"("
    r"\b\d+(?:[,.]\d+)?\s*(?:kg|kgs|g|gr|ud|uds|u|pz|pzs)\b"
    r"|\b\d+\s*[x×]\s*\d+(?:\s*[x×]\s*\d+)?\b"
    r"|\b(?:caja|cajas|palet|pallet|palets|pallets|envase|bandeja|bolsa|saco|malla|granel|encajad\w*|girsac|flowpack|pack|paquete)\b"
    r")"
)
_CALIBRE_VALUE_RE = re.compile(
    r"(?ix)^(?:"
    r"\d+(?:[/-]\d+[A-Z]*)+(?:\s*(?:pz|pzs|mm|cal)\b)?"
    r"|\d+\s*-\s*\d+\s*(?:pz|pzs|mm|cal)?\b"
    r"|del\s+\d+(?:[,.]\d+)?\s+al\s+\d+(?:[,.]\d+)?"
    r")$"
)
_CATEGORY_RE = re.compile(
    r"(?i)\b(?:cat\.?|categor[ií]a|ct\.?)\s*[:\-]?\s*(extra|i{1,3}|iv|v|[0-9]+)\b"
)


def _limpiar_texto(texto: str) -> str:
    limpio = str(texto or "")
    limpio = limpio.replace("\r\n", "\n").replace("\r", "\n")
    limpio = re.sub(r"[ \t]+", " ", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio)
    return limpio.strip()


def _match_group(pattern: str, texto: str, flags: int = re.IGNORECASE) -> str:
    match = re.search(pattern, texto, flags)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _normalizar_numero(value: str) -> str:
    return str(value or "").strip().replace(",", ".")


def _append_unique(values: list[str], value: str) -> None:
    value = str(value or "").strip()
    if value and value not in values:
        values.append(value)


def extraer_cabecera(texto: str) -> dict[str, str]:
    cabecera = {
        "NumeroPedido": _match_group(r"N[ºo]\s*Pedido[:\s]*([\w/.-]+)", texto),
        "Cliente": _match_group(
            r"CLIENTE[:\s]*(.+?)(?=\s+(?:Pos\.?\s+Cliente|F\.\s*Carga|Plataforma|N[ºo]\s*Pedido|P\.?\s*Carga|De:)|$)",
            texto,
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "Comercial": _match_group(
            r"De:\s*(.+?)\s+(?:A[Ee]-?mail|E-?mail|Email|Mail)\b", texto
        ),
        "FechaSalida": _match_group(
            r"F\.\s*Carga[:\s]*(?:[A-Za-záéíóúñÁÉÍÓÚÑ]+\s+)?(\d{1,2}/\d{1,2}/\d{2,4})",
            texto,
        ),
        "PuntoCarga": _match_group(
            r"P\.?\s*Carga[:\s]*(.+?)(?=\s+(?:Plataforma|N[ºo]\s*Pedido|CLIENTE|De:)|$)",
            texto,
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "Plataforma": _match_group(
            r"Plataforma[:\s]*(.+?)(?=\s+(?:N[ºo]\s*Pedido|P\.?\s*Carga|CLIENTE|De:)|$)",
            texto,
            flags=re.IGNORECASE | re.DOTALL,
        ),
    }
    return {
        key: re.sub(r"\s+", " ", str(value or "")).strip()
        for key, value in cabecera.items()
    }


def _extraer_bloque_lineas(texto: str) -> str:
    inicio_match = re.search(r"(?im)^\s*L[ií]n\.?\b.*$", texto)
    if not inicio_match:
        inicio_match = re.search(r"\bL[ií]n\.?\b", texto, re.IGNORECASE)
    if not inicio_match:
        return ""
    return str(texto[inicio_match.end() :] or "").strip()


def _es_inicio_linea_pedido(linea: str) -> bool:
    actual = str(linea or "").strip()
    if not actual or _LABEL_RE.match(actual):
        return False
    if re.match(r"^\d{1,4}\s*$", actual):
        return True
    return bool(
        re.match(r"^\d{1,4}\s+\S.{1,}$", actual) and not _CALIBRE_VALUE_RE.match(actual)
    )


def _segmentar_lineas(bloque: str) -> list[str]:
    lineas = [linea.strip() for linea in bloque.splitlines() if linea.strip()]
    segmentos: list[list[str]] = []
    actual: list[str] = []

    for linea in lineas:
        if _LINE_STOP_RE.match(linea):
            break
        if _es_inicio_linea_pedido(linea) and actual:
            segmentos.append(actual)
            actual = [linea]
            continue
        actual.append(linea)

    if actual:
        segmentos.append(actual)
    return ["\n".join(segmento).strip() for segmento in segmentos if segmento]


def _extraer_linea_y_consumir(filas: list[str]) -> tuple[str, list[str]]:
    if not filas:
        return "", []
    primera = filas[0].strip()
    match_sola = re.match(r"^(\d{1,4})\s*$", primera)
    if match_sola:
        return match_sola.group(1), filas[1:]
    match_prefijo = re.match(r"^(\d{1,4})\s+(.+)$", primera)
    if match_prefijo:
        return match_prefijo.group(1), [primera, *filas[1:]]
    return "", filas


def _extraer_cantidad_palet(filas: list[str]) -> tuple[str, str]:
    for fila in filas:
        if _LABEL_RE.match(fila):
            continue
        match = re.match(r"^(\d+(?:[,.]\d+)?)\s+(.+)$", fila.strip())
        if not match:
            continue
        formato = match.group(2).strip()
        if re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", formato):
            return _normalizar_numero(match.group(1)), formato
    return "", ""


def _extraer_cajas(chunk: str) -> str:
    etiquetada = _match_group(
        r"Total\s+(?:Cajas|Ud\.?\s*Venta)\s*:\s*(\d+(?:[,.]\d+)?)", chunk
    )
    if etiquetada:
        return _normalizar_numero(etiquetada)
    return _normalizar_numero(
        _match_group(r"(?im)^\s*Cajas\s*:?\s*(\d+(?:[,.]\d+)?)\s*$", chunk, flags=0)
    )


def _extraer_calibre(chunk: str) -> str:
    etiquetado = _match_group(
        r"(?im)^\s*(?:Calibre|Cal\.?)\s*:\s*([^\n]+)", chunk, flags=0
    )
    if etiquetado:
        return etiquetado
    for fila in chunk.splitlines():
        actual = fila.strip()
        if _CALIBRE_VALUE_RE.match(actual):
            return actual
    return ""


def _extraer_categoria(chunk: str) -> str:
    match = _CATEGORY_RE.search(chunk)
    if not match:
        return ""
    valor = match.group(1).upper()
    return f"CAT.{valor}" if valor in {"I", "II", "III", "IV", "V", "EXTRA"} else valor


def _extraer_etiqueta(chunk: str, etiqueta: str) -> str:
    return _match_group(rf"(?im)^\s*{etiqueta}\s*:\s*([^\n]+)", chunk, flags=0)


def _es_linea_auxiliar(fila: str) -> bool:
    actual = fila.strip()
    return bool(
        not actual
        or _LABEL_RE.match(actual)
        or re.match(r"(?i)^(?:cat\.?|ct\.?|categor[ií]a)\s*[:\-]?\s*\S+", actual)
        or re.match(r"(?i)^\(\*\)\s*$", actual)
        or re.match(r"(?i)^\d+(?:[,.]\d+)?\s+.+$", actual)
        or _CALIBRE_VALUE_RE.match(actual)
    )


def _extraer_mercancia_confeccion(filas: list[str], chunk: str) -> tuple[str, str]:
    mercancia: list[str] = []
    confeccion: list[str] = []
    tras_marcador = False

    for fila in filas:
        actual = re.sub(r"^\(\*\)\s*", "", fila.strip())
        if actual != fila.strip():
            tras_marcador = True
        if _es_linea_auxiliar(actual):
            continue
        if _PACKAGING_RE.search(actual) or (not tras_marcador and not mercancia):
            _append_unique(confeccion, actual)
            continue
        _append_unique(mercancia, actual)

    if not mercancia:
        marcador = re.search(
            r"\(\*\)\s*(.+?)(?=\n\s*(?:Calibre|Cal\.?|Cat\.?|Ct\.?|Categor[ií]a|Marca|Lote|PO|Observa\w*|Total\s+)|$)",
            chunk,
            re.IGNORECASE | re.DOTALL,
        )
        if marcador:
            mercancia = [re.sub(r"\s+", " ", marcador.group(1)).strip()]

    return " ".join(mercancia).strip(), " ".join(confeccion).strip()


def _linea_minimamente_identificable(linea: dict[str, str]) -> bool:
    if linea.get("Linea") and any(
        linea.get(campo)
        for campo in ("Cantidad", "CajasTotales", "Mercancia", "TipoPalet")
    ):
        return True
    return bool(
        linea.get("Cantidad")
        and (
            linea.get("CajasTotales")
            or linea.get("Mercancia")
            or linea.get("TipoPalet")
        )
    )


def extraer_lineas(texto: str) -> list[dict[str, str]]:
    bloque = _extraer_bloque_lineas(texto)
    if not bloque:
        return []

    lineas: list[dict[str, str]] = []
    for chunk in _segmentar_lineas(bloque):
        filas_originales = [fila.strip() for fila in chunk.splitlines() if fila.strip()]
        linea_num, filas = _extraer_linea_y_consumir(filas_originales)
        cantidad, tipo_palet = _extraer_cantidad_palet(filas)
        cajas = _extraer_cajas(chunk)
        mercancia, confeccion = _extraer_mercancia_confeccion(filas, chunk)
        calibre = _extraer_calibre(chunk)
        categoria = _extraer_categoria(chunk)
        marca = _extraer_etiqueta(chunk, "Marca")
        lote = _extraer_etiqueta(chunk, "Lote")
        po = _extraer_etiqueta(chunk, "PO")
        observaciones = _extraer_etiqueta(chunk, r"Observa\w*")

        cp = ""
        if cantidad and cajas:
            try:
                cp = str(int(float(cajas)) // int(float(cantidad)))
            except Exception:  # noqa: BLE001
                logger.warning("No se pudo calcular CP para línea %s", linea_num or "?")

        linea = {
            "Linea": linea_num,
            "Cantidad": cantidad,
            "TipoPalet": tipo_palet,
            "CajasTotales": cajas,
            "CP": cp,
            "Mercancia": _normalizar_numero(mercancia),
            "Confeccion": _normalizar_numero(confeccion),
            "Calibre": _normalizar_numero(calibre),
            "Categoria": categoria,
            "Marca": marca,
            "PO": po,
            "Lote": lote,
            "Observaciones": observaciones,
        }
        warnings = [
            campo
            for campo in ("Linea", "Cantidad", "CajasTotales", "Mercancia")
            if not linea.get(campo)
        ]
        if warnings:
            logger.warning(
                "Línea de pedido parcial; campos no detectados: %s", ", ".join(warnings)
            )
            linea["Warnings"] = f"Campos no detectados: {', '.join(warnings)}"
        if _linea_minimamente_identificable(linea):
            lineas.append(linea)
    return lineas


def extraer_pedido_desde_pdf(texto: str) -> list[dict[str, Any]]:
    """Extrae pedido y líneas disponibles sin depender de catálogos cerrados.

    Solo descarta el documento cuando no hay número de pedido ni ninguna línea
    mínimamente identificable. Si una línea es parcial, la conserva con los
    campos disponibles y registra un warning.
    """
    texto_limpio = _limpiar_texto(texto)
    cabecera = extraer_cabecera(texto_limpio)
    lineas = extraer_lineas(texto_limpio)

    numero_pedido = str(cabecera.get("NumeroPedido", "")).strip()
    if not numero_pedido and not lineas:
        return []
    if not numero_pedido:
        logger.warning("Pedido sin NumeroPedido; se conservan líneas identificables")
    if numero_pedido and not lineas:
        logger.warning("Pedido %s sin líneas mínimamente identificables", numero_pedido)
        return []

    resultado: list[dict[str, Any]] = []
    for linea in lineas:
        linea_final = {**cabecera, **linea}
        for key in ["Cliente", "Comercial", "FechaSalida", "PuntoCarga", "Plataforma"]:
            linea_final[key] = linea_final.get(key) or cabecera.get(key, "")
        resultado.append(linea_final)
    return resultado
