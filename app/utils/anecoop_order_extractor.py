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

    partes = re.split(r"(Total\s+Cajas\s*:\s*\d+)", bloque, flags=re.IGNORECASE)
    if len(partes) < 3:
        return []

    candidatos: list[str] = []
    for idx in range(1, len(partes), 2):
        previo = str(partes[idx - 1] or "")
        total_cajas = str(partes[idx] or "")
        siguiente = str(partes[idx + 1] or "") if idx + 1 < len(partes) else ""
        corte_siguiente = re.search(r"(?m)^\s*\d{1,4}\s*$", siguiente)
        if corte_siguiente:
            siguiente = siguiente[: corte_siguiente.start()]
        candidato = f"{previo}\n{total_cajas}\n{siguiente}".strip()
        if candidato:
            candidatos.append(candidato)

    for candidato in candidatos:
        chunk = str(candidato or "").strip()
        if not chunk:
            continue
        print("BLOQUE PROCESADO:", chunk)

        linea = _match_group(r"(?m)^\s*(\d{1,4})\s*$", chunk, flags=0)
        if not linea:
            linea = _match_group(r"(?m)^\s*(\d{1,4})\s+EuroChep\w+", chunk, flags=re.IGNORECASE)
        if not linea:
            continue

        cantidad = _match_group(r"(\d+)\s+(EuroChep\w+)", chunk, flags=re.IGNORECASE)
        tipo_palet = _match_group(r"\d+\s+(EuroChep\w+)", chunk, flags=re.IGNORECASE)
        cajas = _match_group(r"Total\s+Cajas\s*:\s*(\d+)", chunk)

        mercancia = ""
        marcador = re.search(r"\(\*\)\s*", chunk)
        if marcador:
            resto = chunk[marcador.end() :]
            lineas_mercancia: list[str] = []
            for linea_bloque in resto.splitlines():
                actual = str(linea_bloque or "").strip()
                if not actual:
                    continue
                if re.match(r"(?i)^Calibre\s*:", actual):
                    break
                if re.match(r"(?i)^Total\s+Cajas\s*:", actual):
                    break
                if re.match(r"(?i)^Observa\w*", actual):
                    break
                lineas_mercancia.append(actual)
            mercancia = " ".join(lineas_mercancia).strip()

        calibre = _match_group(r"Calibre\s*:\s*([^\n]+)", chunk, flags=0)

        cp = ""
        if cantidad and cajas:
            try:
                cp = str(int(float(cajas)) // int(float(cantidad)))
            except Exception:
                pass

        lineas.append(
            {
                "Linea": linea,
                "Cantidad": (cantidad or "").strip().replace(",", "."),
                "TipoPalet": (tipo_palet or "").strip().replace(",", "."),
                "CajasTotales": (cajas or "").strip().replace(",", "."),
                "CP": cp,
                "Mercancia": (mercancia or "").strip().replace(",", "."),
                "Calibre": (calibre or "").strip().replace(",", "."),
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

    if not re.search(r"\d{2}/\d{6}/\d{1,2}", texto_limpio):
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
