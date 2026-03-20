print("🔥 NORMALIZACION PEDIDOS CARGADA 🔥")

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
def normalizar_campos_linea(linea: dict) -> dict:
    print("ANTES:", linea)

    nueva = {}

    for k, v in linea.items():
        key = k.lower().strip()

        encontrado = False

        for campo_std, variantes in MAPEO_CAMPOS_PEDIDO.items():
            if key in variantes:
                nueva[campo_std] = v
                encontrado = True
                break

        if not encontrado:
            nueva[k] = v  # no perder info

    # 🔥 NORMALIZACIÓN EXTRA (valores)

    if "Categoria" in nueva and nueva["Categoria"]:
        nueva["Categoria"] = str(nueva["Categoria"]).strip().upper()

    print("DESPUÉS:", nueva)
    return nueva
