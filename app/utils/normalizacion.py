def normalizar_campos_linea(linea: dict) -> dict:
    nueva = {}

    for k, v in linea.items():
        key = k.lower().strip()

        # 🧠 NORMALIZACIÓN INTELIGENTE

        if key in ["pedido", "pedidoid", "numero", "nº pedido"]:
            nueva["NumeroPedido"] = v

        elif key in ["cliente", "customer"]:
            nueva["Cliente"] = v

        elif key in ["fecha", "fecha salida", "fecha_salida", "fcarga"]:
            nueva["FechaSalida"] = v

        elif key in ["mercancia", "producto", "articulo"]:
            nueva["Mercancia"] = v

        elif key in ["origen", "carga", "punto carga", "pcarga"]:
            nueva["PuntoCarga"] = v

        elif key in ["confeccion", "confección"]:
            nueva["Confeccion"] = v

        elif key in ["categoria", "cat", "cat.", "ct"]:
            nueva["Categoria"] = v

        else:
            nueva[k] = v

    return nueva
