from __future__ import annotations

import unicodedata

MAPEO_CAMPOS_PEDIDO = {
    # 📦 Pedido
    "NumeroPedido": ["pedido", "pedidoid", "numeropedido", "num pedido", "nº pedido"],
    "Cliente": ["cliente", "customer", "client"],
    "Comercial": ["comercial", "comer.", "vendedor"],
    "FechaSalida": ["fecha", "fecha salida", "fecha_salida", "fcarga", "fecha de carga"],
    "Plataforma": ["plataforma", "destino"],
    "Pais": ["pais", "país", "country"],
    "PuntoCarga": ["pcarga", "punto carga", "punto de carga", "origen"],
    "Estado": ["estado", "status"],

    # 📦 Línea
    "Linea": ["linea", "línea"],
    "Cantidad": ["cantidad", "palets", "pallets"],
    "TipoPalet": ["tipopalet", "tipo palet", "pallet tipo"],
    "CajasTotales": ["cajastotales", "tcajas", "cajas"],
    "CP": ["cp", "cajas por pallet"],
    "NombreCaja": ["nombrecaja", "caja", "tipo caja"],
    "Mercancia": ["mercancia", "producto", "articulo", "variedad"],
    "Confeccion": ["confeccion", "confección", "formato"],
    "Calibre": ["calibre", "cal", "cal."],
    "Categoria": ["categoria", "cat", "cat.", "ct"],
    "Marca": ["marca", "brand"],
    "PO": ["po", "pedido compra"],
    "Lote": ["lote", "batch"],
    "Observaciones": ["observaciones", "notas", "comentarios"]
}


def _normalize_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


_VARIANTES_NORMALIZADAS = {
    campo: {_normalize_key(variante) for variante in variantes}
    for campo, variantes in MAPEO_CAMPOS_PEDIDO.items()
}


def normalizar_campos_linea(linea: dict) -> dict:
    nueva = {}

    for k, v in linea.items():
        key = _normalize_key(k)

        encontrado = False

        for campo_std, variantes in _VARIANTES_NORMALIZADAS.items():
            if key in variantes:
                nueva[campo_std] = v
                encontrado = True
                break

        if not encontrado:
            nueva[k] = v  # no perder info

    # 🔥 NORMALIZACIÓN EXTRA (valores)

    if "Categoria" in nueva and nueva["Categoria"]:
        nueva["Categoria"] = str(nueva["Categoria"]).strip().upper()

    return nueva
