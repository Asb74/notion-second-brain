"""Extractor específico para pedidos tipo Anecoop."""

from __future__ import annotations

import re
from typing import Any


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

    inicio_match = re.search(r"\bL[ií]n\.?\b", texto, re.IGNORECASE)
    if not inicio_match:
        return []

    bloque = str(texto[inicio_match.end() :] or "")
    fin_match = re.search(r"(?im)^\s*Observa\w*[^\n]*$", bloque)
    if fin_match:
        bloque = bloque[: fin_match.start()]
    bloque = bloque.strip()
    print("BLOQUE LINEAS:", bloque)
    if not bloque:
        return []

    candidatos = re.split(r"\n(?=\s*\d+\s+EuroChep)", bloque, flags=re.IGNORECASE)
    for candidato in candidatos:
        chunk = str(candidato or "").strip()
        print("CHUNK:", chunk)
        if not chunk:
            continue

        linea = _match_group(r"^\s*(\d{1,4})\b", chunk, flags=re.MULTILINE)
        if not linea:
            continue

        cantidad = _match_group(r"^\s*\d{1,4}\s+(\d+(?:[.,]\d+)?)\b", chunk, flags=re.MULTILINE)
        if not cantidad:
            cantidad = _match_group(r"^\s*(\d+(?:[.,]\d+)?)\s+[A-Za-z][\w./-]*\b", chunk, flags=re.MULTILINE)

        tipo_palet = _match_group(r"^\s*\d{1,4}\s+([A-Za-z][\w./-]*)\b", chunk, flags=re.MULTILINE)
        if not tipo_palet:
            tipo_palet = _match_group(r"^\s*\d{1,4}\s+\d+(?:[.,]\d+)?\s+([A-Za-z][\w./-]*)\b", chunk, flags=re.MULTILINE)

        cajas = _match_group(r"Total\s+Cajas\s*:\s*(\d+(?:[.,]\d+)?)", chunk)
        mercancia_match = re.search(
            r"(?im)^\s*\(\*\)\s*(?P<base>[^\n]*)(?P<rest>(?:\n(?!\s*(?:Calibre|Total\s*Cajas|Observa\w*|\d+\s+[A-Za-z]))[^\n]+)*)",
            chunk,
        )
        mercancia = ""
        if mercancia_match:
            partes = [mercancia_match.group("base") or ""]
            resto = mercancia_match.group("rest") or ""
            if resto:
                partes.extend(linea.strip() for linea in resto.splitlines())
            mercancia = " ".join(parte.strip() for parte in partes if parte and parte.strip())
        calibre = _match_group(r"Calibre\s*:\s*([^\n]+)", chunk)

        lineas.append(
            {
                "Linea": linea,
                "Cantidad": (cantidad or "").replace(",", "."),
                "TipoPalet": tipo_palet,
                "CajasTotales": (cajas or "").replace(",", "."),
                "Mercancia": mercancia,
                "Calibre": calibre,
            }
        )
    return lineas


def extraer_pedido_desde_pdf(texto: str) -> list[dict[str, Any]]:
    """Extrae pedido Anecoop y devuelve una lista de líneas canonical-like.

    Reglas:
    - Si no hay número de pedido, no se devuelve nada.
    - Si no hay líneas válidas en el bloque Lin./Observaciones, no se devuelve nada.
    """
    texto_limpio = _limpiar_texto(texto)
    print("=== DEBUG PDF ===")

    if "Anecoop" not in texto_limpio and "ORDEN DE PEDIDO" not in texto_limpio:
        return []

    cabecera = extraer_cabecera(texto_limpio)
    print("CABECERA:", cabecera)

    numero_pedido = str(cabecera.get("NumeroPedido", "")).strip()
    if not numero_pedido:
        return []

    lineas = extraer_lineas(texto_limpio)
    print("LINEAS:", lineas)

    if not lineas:
        return []

    resultado: list[dict[str, Any]] = []
    for linea in lineas:
        linea_final = {**cabecera, **linea}
        for key in ["Cliente", "Comercial", "FechaSalida", "PuntoCarga"]:
            linea_final[key] = linea_final.get(key) or cabecera.get(key, "")
        if not linea_final.get("NumeroPedido"):
            continue
        resultado.append(linea_final)
    return resultado
