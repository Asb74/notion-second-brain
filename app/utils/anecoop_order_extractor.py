"""Extractor tolerante para pedidos tipo Anecoop.

El parser evita catálogos cerrados: no valida clientes, productos, marcas,
confecciones ni tipos de palet contra listas fijas. Extrae por etiquetas,
posición en el bloque y patrones generales de formato y devuelve las líneas en
el formato interno usado por Notion Second Brain.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.utils.normalizacion_pedido import normalizar_campos_linea

logger = logging.getLogger(__name__)

ORDER_ID_RE = r"\d{2}/\d{5,6}/\d{1,3}|[A-Z0-9][\w.-]*/[\w./-]+"
HEADER_STOP = (
    r"CLIENTE|Cliente|Pos\.?\s*Cliente|N[º°o]\s*Pedido|Num\.?\s*Pedido|F\.?\s*Carga|"
    r"P\.?\s*Carga|Punto\s+de\s+Carga|Plataforma|F\.?\s*Llegada|De:|Comercial|Página|Pagina|Lin\."
)
LABEL_LINE_RE = re.compile(
    r"(?i)^\s*(?:calibre|cal\.?|cat\.?|ct\.?|categor[ií]a|marca|lote|po|observa\w*|"
    r"caja|etiq\.?\s*caja|pz/uv|total\s+(?:cajas|ud\.?\s*venta))\s*:"
)
TOTAL_RE = re.compile(r"(?i)Total\s+(?:Cajas|Ud\.?\s*Venta)\s*:\s*(\d+(?:[,.]\d+)?)")
PALLET_STRUCT_RE = re.compile(
    r"(?i)^\s*(\d+(?:[,.]\d+)?)\s*(?:P\.?\s*x\s*(\d+))\s*(?:(\d+(?:[,.]\d+)?))?\s*$"
)
PALLET_TEXT_RE = re.compile(r"(?i)^\s*(\d+(?:[,.]\d+)?)\s+(.+?)(?:\s*[x×]\s*(\d+))\s*$")
CALIBRE_RE = re.compile(
    r"(?ix)(?:"
    r"Del\s+\d+(?:[/-]\d+)?(?:[A-Za-z]*)\s+al\s+\d+(?:[/-]\d+)?(?:[A-Za-z]*)"
    r"|\d+\s*/\s*\d+(?:\s*\([^)]+\))?"
    r"|\d+\s*-\s*\d+\s*Pz?s?\.?(?:\s+al\s+\d+\s*-\s*\d+\s*Pz?s?\.?)?"
    r"|\d+\s*[x×]\s*\([^)]+\)\s*Pz?s?\.?(?:\s+al\s+\d+\s*[x×]\s*\([^)]+\)\s*Pz?s?\.?)?"
    r")"
)
CATEGORY_TOKEN_RE = re.compile(r"(?i)^(I{1,2}|EXTRA|ESTANDAR)$")
CATEGORY_LABEL_RE = re.compile(
    r"(?i)\b(?:Cat\.?|Ct\.?|Categor[ií]a)\s*[:.-]?\s*(I{1,2}|EXTRA|ESTANDAR)\b"
)
PACKAGING_RE = re.compile(
    r"(?ix)("
    r"\b\d+\s*[x×]\s*\w+|\b\d+(?:[,.]\d+)?\s*(?:kg|kgs|g|gr|pz|pzs|ud|uds)\b|"
    r"\b(?:malla|girsac|encajad\w*|granel|alveolos|cart[oó]n?|plastico|pl[aá]stico|ifco|bll|boca|caja|bolsa|saco|pack)\b|"
    r"\b\d+\s*[x×]\s*\d+(?:\s*[x×]\s*\d+(?:[,.]\d+)?)?\b"
    r")"
)
OBS_RE = re.compile(
    r"(?i)\b(?:GLOBALG\.A\.P\.|Idioma|IAN\b|Ean\s+UV|Malla|Caja\s+(?:verde|roja|azul|negra|blanca)|BRIX|ZUMO|"
    r"Precio\s+Mercado|EUR\s+x|L\s*I\s*N\s*E\s*A\s*--\s*C\s*A\s*N\s*C\s*E\s*L\s*A\s*D\s*A)\b"
)
NO_LINE_START_RE = re.compile(
    r"(?i)\b(?:Total\s+Cajas|Total\s+Ud\.?\s*Venta|Etiq\.?\s*Caja|Origen|"
    r"Observaciones?|IAN|Ean\s+UV|BRIX|ZUMO)\b"
)
COMPACT_ORDER_LINE_RE = re.compile(
    r"(?is)^\s*(?P<cantidad>\d+(?:[,.]\d+)?)\s+"
    r"(?P<palet>\S+)\s+"
    r"(?P<mercancia>.*?)\s+"
    r"(?P<calibre>Del\s+\d+(?:[/-]\d+)?(?:[A-Za-z]*)\s+al\s+\d+(?:[/-]\d+)?(?:[A-Za-z]*))"
    r"\s*\|\s*PZ/UV\s*:\s*(?P<marca>.*?)(?=\s+Precio\s+Mercado|\s*/|$)"
    r"(?P<resto>.*)$"
)
SIMPLE_PACKAGING_RE = re.compile(
    r"(?is)^\s*(?P<modo>Simple|Doble|Triple)\s+(?P<confeccion>.*?)(?:\s+Caja\s*:\s*(?P<caja>[^\n]+))?\s*$"
)

CONTRACT_TO_INTERNAL = {
    "PedidoID": "NumeroPedido",
    "Palets": "Cantidad",
    "NombrePalet": "TipoPalet",
    "TCajas": "CajasTotales",
    "FCarga": "FechaSalida",
    "PCarga": "PuntoCarga",
}


def _limpiar_texto(texto: str) -> str:
    limpio = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")
    limpio = re.sub(r"[ \t]+", " ", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio)
    return limpio.strip()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" -:\t\n")


def _match(pattern: str, texto: str, flags: int = re.IGNORECASE) -> str:
    found = re.search(pattern, texto, flags)
    return _clean(found.group(1)) if found else ""


def _num(value: str) -> str:
    value = _clean(value).replace(",", ".")
    if re.fullmatch(r"\d+\.0+", value):
        return value.split(".", 1)[0]
    return value


def _as_int_text(value: str) -> str:
    number = _num(value)
    try:
        return str(int(float(number)))
    except ValueError:
        return number


def _split_plataforma_pais(value: str) -> tuple[str, str]:
    value = _clean(value)
    if not value:
        return "", ""
    match = re.match(r"(.+?)\s*\(([^()]+)\)\s*$", value)
    if match:
        return _clean(match.group(1)), _clean(match.group(2)).upper()
    return value, ""


def _estado_documento(texto: str) -> str:
    if re.search(r"(?i)\b(?:Rectificaci[oó]n|RECTIFICADO|MODIFICADO)\b", texto):
        return "RECTIFICADO"
    if re.search(r"(?i)\b(?:pedido\s+)?cancelad[oa]\b", texto) and not re.search(
        r"(?i)L\s*I\s*N\s*E\s*A\s*--\s*C\s*A\s*N\s*C\s*E\s*L\s*A\s*D\s*A", texto
    ):
        return "CANCELADO"
    return "Nuevo"


def extraer_cabecera(texto: str) -> dict[str, str]:
    numero = _match(
        rf"(?:N[º°o]\s*Pedido|Num\.?\s*Pedido)\s*:?\s*({ORDER_ID_RE})", texto
    )
    cliente = _match(
        rf"CLIENTE\s*:\s*(.+?)(?=\n|\s+(?:{HEADER_STOP})\b|$)",
        texto,
        re.IGNORECASE,
    )
    if not cliente:
        cliente = _cliente_por_posicion(texto)

    fcarga = _match(
        rf"F\.?\s*Carga\s*:?\s*(.+?)(?=\n|\s+(?:{HEADER_STOP})\b|$)",
        texto,
        re.IGNORECASE,
    )
    plataforma_raw = _match(
        rf"Plataforma\s*:\s*(.+?)(?=\n|\s+(?:{HEADER_STOP})\b|$)",
        texto,
        re.IGNORECASE,
    )
    plataforma, pais = _split_plataforma_pais(plataforma_raw)
    pcarga = _normalizar_punto_carga(
        _match(
            rf"(?:P\.?\s*Carga|Punto\s+de\s+Carga)\s*:?\s*(.+?)(?=\n|\s+(?:{HEADER_STOP})\b|$)",
            texto,
            re.IGNORECASE,
        )
    )
    comercial = _match(
        r"(?:De:|Comercial\s*:)\s*(.+?)(?=\s+(?:A[Ee]-?mail|E-?mail|Email|Mail|CLIENTE|N[º°o]\s*Pedido)\b|$)",
        texto,
        re.IGNORECASE,
    )
    cabecera = {
        "PedidoID": numero,
        "Cliente": cliente,
        "Comercial": comercial,
        "FCarga": fcarga,
        "Plataforma": plataforma,
        "Pais": pais,
        "PCarga": pcarga,
        "Estado": _estado_documento(texto),
    }
    logger.info("CABECERA EXTRAIDA %s", cabecera)
    return cabecera


def _cliente_por_posicion(texto: str) -> str:
    candidatas = []
    for fila in texto.splitlines()[:12]:
        actual = _clean(fila)
        if not actual or re.search(
            rf"(?i)({HEADER_STOP}|Anecoop|ORDEN\s+DE\s+PEDIDO)", actual
        ):
            continue
        if re.search(r"(?i)^(?:C/|Calle|Avda?\.?|Pol\.?|CP\b|Tel\b)", actual):
            break
        if "C/ Monforte" in actual:
            break
        candidatas.append(actual)
    return _clean(" ".join(candidatas[:2]))


def _lineas_utiles(texto: str) -> list[str]:
    return [_clean(linea) for linea in texto.splitlines() if _clean(linea)]


def _extraer_bloque_lineas(texto: str) -> list[str]:
    lineas = _lineas_utiles(texto)
    for index, fila in enumerate(lineas):
        if re.match(r"(?i)^L[ií]n\.?(?:\b|$)", fila):
            return lineas[index + 1 :]
    for index, fila in enumerate(lineas):
        if _es_inicio_linea_real(lineas, index):
            return lineas[index:]
    return []


def _es_calibre(texto: str) -> bool:
    texto = _clean(texto)
    if not texto:
        return False
    resto = CALIBRE_RE.search(texto)
    return bool(resto and resto.start() == 0)


def _tiene_senales_linea(filas: list[str]) -> bool:
    ventana = "\n".join(filas[:12])
    return bool(
        TOTAL_RE.search(ventana)
        or PALLET_STRUCT_RE.search(ventana)
        or any(PALLET_TEXT_RE.match(fila) for fila in filas[:4])
        or re.search(r"(?i)(Caja\s*:|Etiq\.?\s*Caja\s*:|Pz/UV\s*:|Marca\s*:)", ventana)
        or any(PACKAGING_RE.search(fila) for fila in filas[:8])
    )


def _es_inicio_linea_real(lineas: list[str], index: int) -> bool:
    fila = lineas[index]
    if NO_LINE_START_RE.search(fila) or LABEL_LINE_RE.match(fila) or _es_calibre(fila):
        return False
    if re.fullmatch(r"\d{1,3}", fila):
        return _tiene_senales_linea(lineas[index + 1 :])
    prefijo = re.match(r"^\s*(\d{1,3})\s+(.+)$", fila)
    if prefijo and not _es_calibre(prefijo.group(2)):
        return _tiene_senales_linea([prefijo.group(2), *lineas[index + 1 :]])
    return False


def _segmentar_lineas(texto: str) -> list[str]:
    lineas = _extraer_bloque_lineas(texto)
    segmentos: list[list[str]] = []
    actual: list[str] = []
    for index, fila in enumerate(lineas):
        if re.match(r"(?i)^Observaciones\s*$", fila) and not actual:
            break
        if _es_inicio_linea_real(lineas, index):
            if actual and not (
                len(actual) == 1 and re.fullmatch(r"\d{1,3}", actual[0])
            ):
                segmentos.append(actual)
                actual = [fila]
                continue
            if not actual:
                actual = [fila]
                continue
        if actual:
            actual.append(fila)
    if actual:
        segmentos.append(actual)
    bloques = [
        "\n".join(segmento).strip()
        for segmento in segmentos
        if _tiene_senales_linea(segmento)
    ]
    logger.info("BLOQUES LINEA DETECTADOS %s", len(bloques))
    return bloques


def _extraer_palets(filas: list[str]) -> tuple[str, str, str, str, int | None]:
    for index, fila in enumerate(filas):
        match_struct = PALLET_STRUCT_RE.match(fila)
        if match_struct:
            return (
                _as_int_text(match_struct.group(1)),
                "",
                _as_int_text(match_struct.group(2)),
                _as_int_text(match_struct.group(3) or ""),
                index,
            )
        match_text = PALLET_TEXT_RE.match(fila)
        if match_text and re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", match_text.group(2)):
            return (
                _as_int_text(match_text.group(1)),
                _clean(match_text.group(2)),
                _as_int_text(match_text.group(3)),
                "",
                index,
            )
        match_generic = re.match(r"^\s*(\d+(?:[,.]\d+)?)\s+(.+)$", fila)
        if match_generic and re.search(
            r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", match_generic.group(2)
        ):
            return (
                _as_int_text(match_generic.group(1)),
                _clean(match_generic.group(2)),
                "",
                "",
                index,
            )
    return "", "", "", "", None


def _extraer_total_cajas(chunk: str, total_struct: str) -> str:
    etiquetada = TOTAL_RE.search(chunk)
    if etiquetada:
        return _as_int_text(etiquetada.group(1))
    return _as_int_text(total_struct)


def _calcular_cp(palets: str, tcajas: str, cp: str) -> str:
    if cp:
        return _as_int_text(cp)
    if not palets or not tcajas:
        return ""
    try:
        palets_int = int(float(palets))
        tcajas_int = int(float(tcajas))
    except ValueError:
        return ""
    if palets_int <= 0 or tcajas_int % palets_int:
        return ""
    return str(tcajas_int // palets_int)


def _extraer_nombre_palet(nombre: str, filas: list[str], index: int | None) -> str:
    nombre = re.sub(r"(?i)\s*[x×]\s*\d+\s*$", "", _clean(nombre)).strip()
    if index is not None and index + 1 < len(filas):
        siguiente = filas[index + 1]
        if (
            siguiente
            and not LABEL_LINE_RE.match(siguiente)
            and not PACKAGING_RE.search(siguiente)
            and not _es_calibre(siguiente)
            and len(siguiente.split()) <= 4
        ):
            nombre = _clean(f"{nombre} {siguiente}")
    return nombre


def _label(chunk: str, etiqueta: str) -> str:
    return _match(rf"(?im)^\s*{etiqueta}\s*:\s*([^\n]+)", chunk, 0)


def _extraer_nombre_caja(chunk: str) -> str:
    return _label(chunk, r"(?:Etiq\.?\s*)?Caja")


def _extraer_marca(chunk: str, nombre_caja: str) -> str:
    return _label(chunk, "Marca") or _label(chunk, r"Pz/UV") or nombre_caja


def _extraer_lote(chunk: str) -> str:
    return _label(chunk, "Lote") or _match(r"\b(L-[A-Z0-9-]+)\b", chunk, re.IGNORECASE)


def _extraer_po(chunk: str) -> str:
    return _label(chunk, "PO")


def _separar_categoria(texto: str) -> tuple[str, str]:
    actual = _clean(texto)
    if not actual:
        return "", ""
    labeled = CATEGORY_LABEL_RE.search(actual)
    if labeled:
        return _clean(CATEGORY_LABEL_RE.sub("", actual)), labeled.group(1).upper()
    match = re.search(r"(?i)\s+(I{1,2}|EXTRA|ESTANDAR)\s*$", actual)
    if match:
        return _clean(actual[: match.start()]), match.group(1).upper()
    # Categoría pegada a la confección: ... CARTONI / CARTONII
    match = re.search(
        r"(?i)(.+(?:CARTON|CART|PLASTICO|PLÁSTICO|IFCO|BLL|BOCA|MALLA|GIRSAC))(I{1,2})$",
        actual,
    )
    if match:
        return _clean(match.group(1)), match.group(2).upper()
    if CATEGORY_TOKEN_RE.fullmatch(actual):
        return "", actual.upper()
    return actual, ""


def _extraer_calibre_categoria(chunk: str) -> tuple[str, str]:
    categoria = ""
    labeled = CATEGORY_LABEL_RE.search(chunk)
    if labeled:
        categoria = labeled.group(1).upper()
    for fila in chunk.splitlines():
        actual = _clean(fila)
        if re.match(r"(?i)^Cal(?:ibre|\.?)\s*:", actual):
            valor = re.sub(r"(?i)^Cal(?:ibre|\.?)\s*:\s*", "", actual)
            valor, cat = _separar_categoria(valor)
            return valor, categoria or cat
        match = CALIBRE_RE.search(actual)
        if match and match.start() == 0:
            resto = _clean(actual[match.end() :])
            _, cat = _separar_categoria(resto)
            return _clean(match.group(0)), categoria or cat
    return "", categoria


def _es_fila_no_producto(fila: str) -> bool:
    return bool(
        LABEL_LINE_RE.match(fila)
        or TOTAL_RE.search(fila)
        or PALLET_STRUCT_RE.match(fila)
        or PALLET_TEXT_RE.match(fila)
        or _es_calibre(fila)
        or CATEGORY_LABEL_RE.search(fila)
        or OBS_RE.search(fila)
        or re.match(r"(?i)^(?:Simple|Doble|Triple|Observaciones?)$", fila)
    )


def _extraer_mercancia_confeccion(
    filas: list[str], pallet_index: int | None
) -> tuple[str, str, str]:
    candidatas: list[str] = []
    for index, fila in enumerate(filas):
        actual = re.sub(r"^\(\*\)\s*", "", fila).strip()
        if index == pallet_index or (
            pallet_index is not None
            and index == pallet_index + 1
            and re.match(r"(?i)^(Simple|Doble|Triple)$", fila)
        ):
            continue
        if _es_fila_no_producto(actual):
            continue
        candidatas.append(actual)

    confeccion = ""
    categoria = ""
    mercancia_partes: list[str] = []
    for candidata in candidatas:
        texto_sin_cat, cat = _separar_categoria(candidata)
        if cat and not categoria:
            categoria = cat
        if PACKAGING_RE.search(texto_sin_cat) and not confeccion:
            confeccion = texto_sin_cat
            continue
        if not confeccion and PACKAGING_RE.search(candidata):
            confeccion = candidata
            continue
        if not PACKAGING_RE.search(candidata):
            mercancia_partes.append(candidata)
    return _clean(" ".join(mercancia_partes)), _clean(confeccion), categoria


def _extraer_observaciones(chunk: str) -> str:
    observaciones: list[str] = []
    for fila in chunk.splitlines():
        actual = _clean(fila)
        if TOTAL_RE.search(actual) or re.search(r"(?i)\b(?:Etiq\.?\s*Caja|Origen)\b", actual):
            continue
        if re.match(r"(?i)^Observa\w*\s*:", actual):
            observaciones.append(re.sub(r"(?i)^Observa\w*\s*:\s*", "", actual))
        elif OBS_RE.search(actual):
            observaciones.append(actual)
    return " // ".join(dict.fromkeys(filter(None, observaciones)))


def _normalizar_punto_carga(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return _clean(
        re.split(
            r"(?i)\s+SIN\s+TRANSBORD\w*|\s+DIRECTO\s+A\b",
            value,
            maxsplit=1,
        )[0]
    )


def _formatear_tipo_palet_compacto(palet_raw: str, modo: str) -> str:
    base = re.sub(r"(?i)[x×]\s*\d+\s*$", "", _clean(palet_raw)).strip()
    if base and not base.endswith("."):
        base = f"{base}."
    return _clean(f"{base} {modo}")


def _parsear_linea_compacta_lidl(
    filas: list[str], estado_pedido: str, linea_num: str
) -> dict[str, str] | None:
    if not filas:
        return None
    match = COMPACT_ORDER_LINE_RE.match(filas[0])
    if not match:
        return None

    cantidad = _as_int_text(match.group("cantidad"))
    palet_raw = _clean(match.group("palet"))
    mercancia = _clean(match.group("mercancia"))
    calibre = _clean(match.group("calibre"))
    marca = _clean(match.group("marca"))
    resto = _clean(match.group("resto"))
    po = "Precio Mercado" if re.search(r"(?i)\bPrecio\s+Mercado\b", resto) else ""
    lote = _match(r"/\s*([A-Z0-9][A-Z0-9.-]*)\b", resto, re.IGNORECASE)

    modo = ""
    confeccion = ""
    nombre_caja = ""
    for fila in filas[1:]:
        packaging = SIMPLE_PACKAGING_RE.match(fila)
        if packaging:
            modo = _clean(packaging.group("modo"))
            confeccion = _clean(packaging.group("confeccion"))
            nombre_caja = _clean(packaging.group("caja"))
            break

    chunk = "\n".join(filas)
    tcajas = _extraer_total_cajas(chunk, "")
    cp = _calcular_cp(cantidad, tcajas, "")
    if modo:
        tipo_palet = _formatear_tipo_palet_compacto(palet_raw, modo)
    else:
        tipo_palet = _extraer_nombre_palet(palet_raw, filas, 0)

    return {
        "Linea": linea_num,
        "Palets": cantidad,
        "NombrePalet": tipo_palet,
        "TCajas": tcajas,
        "CP": cp,
        "NombreCaja": nombre_caja or _extraer_nombre_caja(chunk),
        "Mercancia": mercancia,
        "Confeccion": confeccion,
        "Calibre": calibre,
        "Categoria": "I",
        "Marca": marca or _extraer_marca(chunk, nombre_caja),
        "PO": po or _extraer_po(chunk),
        "Lote": lote or _extraer_lote(chunk),
        "Observaciones": _extraer_observaciones("\n".join(filas[1:])),
        "Estado": _estado_linea(estado_pedido, chunk, linea_num),
    }


def _estado_linea(estado_pedido: str, chunk: str, linea: str) -> str:
    if re.search(
        r"(?i)L\s*I\s*N\s*E\s*A\s*--\s*C\s*A\s*N\s*C\s*E\s*L\s*A\s*D\s*A", chunk
    ):
        return f"CANCELADA LINEA {linea}" if linea else "CANCELADA LINEA"
    return estado_pedido


def _linea_minimamente_identificable(linea: dict[str, str]) -> bool:
    return bool(
        linea.get("Linea")
        or linea.get("Palets")
        or linea.get("TCajas")
        or linea.get("Mercancia")
    )


def _normalizar_linea_contrato(linea: dict[str, str]) -> dict[str, Any]:
    premap = {CONTRACT_TO_INTERNAL.get(key, key): value for key, value in linea.items()}
    return normalizar_campos_linea(premap)


def extraer_lineas(texto: str, estado_pedido: str = "Nuevo") -> list[dict[str, Any]]:
    lineas: list[dict[str, Any]] = []
    for index_linea, chunk in enumerate(_segmentar_lineas(texto), start=1):
        filas = _lineas_utiles(chunk)
        if not filas:
            continue
        linea_num = ""
        filas_datos = filas
        linea_compacta = _parsear_linea_compacta_lidl(
            filas, estado_pedido, str(index_linea)
        )
        if linea_compacta:
            normalizada = _normalizar_linea_contrato(linea_compacta)
            logger.info("LINEA NORMALIZADA %s", normalizada)
            lineas.append(normalizada)
            continue
        if re.fullmatch(r"\d{1,3}", filas[0]):
            linea_num = filas[0]
            filas_datos = filas[1:]
        else:
            prefijo = re.match(r"^\s*(\d{1,3})\s+(.+)$", filas[0])
            if prefijo and not _es_calibre(prefijo.group(2)):
                linea_num = prefijo.group(1)
                filas_datos = [filas[0], *filas[1:]]
        palets, palet_raw, cp_raw, tcajas_raw, pallet_index = _extraer_palets(
            filas_datos
        )
        tcajas = _extraer_total_cajas(chunk, tcajas_raw)
        cp = _calcular_cp(palets, tcajas, cp_raw)
        nombre_palet = _extraer_nombre_palet(palet_raw, filas_datos, pallet_index)
        calibre, categoria_calibre = _extraer_calibre_categoria(chunk)
        mercancia, confeccion, categoria_conf = _extraer_mercancia_confeccion(
            filas_datos, pallet_index
        )
        nombre_caja = _extraer_nombre_caja(chunk)
        linea = {
            "Linea": linea_num,
            "Palets": palets,
            "NombrePalet": nombre_palet,
            "TCajas": tcajas,
            "CP": cp,
            "NombreCaja": nombre_caja,
            "Mercancia": mercancia,
            "Confeccion": confeccion,
            "Calibre": calibre,
            "Categoria": categoria_calibre or categoria_conf,
            "Marca": _extraer_marca(chunk, nombre_caja),
            "PO": _extraer_po(chunk),
            "Lote": _extraer_lote(chunk),
            "Observaciones": _extraer_observaciones(chunk),
            "Estado": _estado_linea(estado_pedido, chunk, linea_num),
        }
        if not _linea_minimamente_identificable(linea):
            continue
        warnings = [
            campo
            for campo in ("Linea", "Palets", "TCajas", "Mercancia")
            if not linea.get(campo)
        ]
        normalizada = _normalizar_linea_contrato(linea)
        if warnings:
            logger.warning(
                "Línea de pedido parcial; campos no detectados: %s", ", ".join(warnings)
            )
            normalizada["Warnings"] = f"Campos no detectados: {', '.join(warnings)}"
        logger.info("LINEA NORMALIZADA %s", normalizada)
        lineas.append(normalizada)
    return lineas


def extraer_pedido_desde_pdf(texto: str) -> list[dict[str, Any]]:
    """Extrae líneas de pedido Anecoop ya normalizadas para persistencia."""
    texto_limpio = _limpiar_texto(texto)
    cabecera_contrato = extraer_cabecera(texto_limpio)
    cabecera = _normalizar_linea_contrato(cabecera_contrato)
    numero_pedido = _clean(cabecera.get("NumeroPedido", ""))
    if not numero_pedido:
        logger.warning("Pedido Anecoop descartado: no se detectó NumeroPedido/PedidoID")
        logger.info("RESULTADO FINAL []")
        return []

    lineas = extraer_lineas(texto_limpio, cabecera.get("Estado", "Nuevo"))
    if not lineas:
        logger.warning("Pedido %s sin líneas mínimamente identificables", numero_pedido)
        logger.info("RESULTADO FINAL []")
        return []

    resultado: list[dict[str, Any]] = []
    for linea in lineas:
        linea_final = {**cabecera, **linea}
        linea_final["NumeroPedido"] = numero_pedido
        resultado.append(linea_final)
    logger.info("RESULTADO FINAL %s", resultado)
    return resultado
