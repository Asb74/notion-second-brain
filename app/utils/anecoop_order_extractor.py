"""Extractor específico para pedidos tipo Anecoop."""

from __future__ import annotations

import re
from typing import Any


def _limpiar_texto(texto: str) -> str:
    limpio = str(texto or "")
    limpio = limpio.replace("\n", " ")
    limpio = re.sub(r"\s+", " ", limpio)
    return limpio.strip()


def _match_group(pattern: str, texto: str, flags: int = re.IGNORECASE) -> str:
    match = re.search(pattern, texto, flags)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def extraer_cabecera(texto: str) -> dict[str, str]:
    cabecera = {
        "NumeroPedido": _match_group(r"N[ºo]\s*Pedido[:\s]*([\d/]+)", texto),
        "Cliente": _match_group(r"CLIENTE[:\s]*([A-Z0-9\s]+?)(?=\s+Pos\.?\s+Cliente|\s+F\.\s*Carga|\s+Plataforma|\s+N[ºo]\s*Pedido|$)", texto, flags=0),
        "Comercial": _match_group(r"De:\s*([A-Za-z\s]+?)\s+A[Ee]-?mail", texto),
        "FechaSalida": _match_group(r"F\.\s*Carga[:\s]*[A-Za-záéíóúñÁÉÍÓÚÑ]+\s+(\d{2}/\d{2}/\d{4})", texto),
        "PuntoCarga": _match_group(r"P\.?\s*Carga[:\s]*([A-ZÁÉÍÓÚÑ\s]+?)(?=\s+Plataforma|\s+N[ºo]\s*Pedido|\s+De:|$)", texto, flags=0),
        "Plataforma": _match_group(r"Plataforma[:\s]*([A-Z0-9\-_/ ]+?)(?=\s+N[ºo]\s*Pedido|\s+P\.?\s*Carga|\s+De:|$)", texto),
    }
    return {key: str(value or "").strip() for key, value in cabecera.items()}


def extraer_lineas(texto: str) -> list[dict[str, str]]:
    lineas: list[dict[str, str]] = []
    # Patrón tolerante para líneas tipo: "1 10 300 NARANJA CAL 3"
    pattern = re.compile(
        r"\b(?P<linea>\d{1,3})\s+"
        r"(?P<cantidad>\d+(?:[.,]\d+)?)\s+"
        r"(?P<cajas>\d+(?:[.,]\d+)?)\s+"
        r"(?P<mercancia>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9\s\-]{2,}?)"
        r"(?:\s+(?P<calibre>(?:CAL\.?|CAT\.?|CT\.?)?\s*[A-Z0-9./-]{1,10}))?"
        r"(?=\s+\d{1,3}\s+\d+(?:[.,]\d+)?\s+\d+(?:[.,]\d+)?\s+[A-ZÁÉÍÓÚÑ]|$)",
        re.IGNORECASE,
    )

    for match in pattern.finditer(texto):
        lineas.append(
            {
                "Linea": str(match.group("linea") or "").strip(),
                "Cantidad": str(match.group("cantidad") or "").replace(",", ".").strip(),
                "CajasTotales": str(match.group("cajas") or "").replace(",", ".").strip(),
                "Mercancia": str(match.group("mercancia") or "").strip(),
                "Calibre": str(match.group("calibre") or "").strip(),
            }
        )
    return lineas


def extraer_pedido_desde_pdf(texto: str) -> list[dict[str, Any]]:
    """Extrae pedido Anecoop y devuelve una lista de líneas canonical-like.

    Reglas:
    - Si no hay número de pedido, no se devuelve nada.
    - Si faltan campos de cabecera pero existe número de pedido, se conserva como incompleto.
    """
    texto_limpio = _limpiar_texto(texto)
    print("TEXTO LIMPIO:", texto_limpio)

    if "Anecoop" not in texto_limpio and "ORDEN DE PEDIDO" not in texto_limpio:
        return []

    cabecera = extraer_cabecera(texto_limpio)
    print("CABECERA EXTRAIDA:", cabecera)

    numero_pedido = str(cabecera.get("NumeroPedido", "")).strip()
    if not numero_pedido:
        return []

    lineas = extraer_lineas(texto_limpio)
    print("LINEAS EXTRAIDAS:", lineas)

    if not lineas:
        lineas = [{}]

    resultado: list[dict[str, Any]] = []
    for linea in lineas:
        linea_final = {**cabecera, **linea}
        if not linea_final.get("NumeroPedido"):
            continue
        if any(not str(linea_final.get(k, "")).strip() for k in ("Cliente", "Comercial", "FechaSalida", "PuntoCarga")):
            linea_final["Estado"] = str(linea_final.get("Estado") or "Incompleto")
        resultado.append(linea_final)
    return resultado

