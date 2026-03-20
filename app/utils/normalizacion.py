def normalizar_campos_linea(linea: dict) -> dict:
    nueva = {}

    for k, v in linea.items():
        key = k.lower().strip()

        # 🧠 NORMALIZACIÓN INTELIGENTE

        if key in ["pedido", "pedidoid", "numero", "nº pedido"]:
            nueva["NumeroPedido"] = v

        elif key in ["cliente", "customer", "Cliente", "Client"]:
            nueva["Cliente"] = v

        elif key in ["fecha", "fecha salida", "fecha_salida", "fcarga", "fecha de carga"]:
            nueva["FechaSalida"] = v

        elif key in ["mercancia", "producto", "articulo", "mercancía"]:
            nueva["Mercancia"] = v

        elif key in ["origen", "carga", "punto carga", "pcarga", "punto de carga"]:
            nueva["PuntoCarga"] = v

        elif key in ["confeccion", "confección"]:
            nueva["Confeccion"] = v

        elif key in ["categoria", "cat", "cat.", "ct"]:
            nueva["Categoria"] = v
            
        elif key in ["calibre", "cal", "cal."]:
            nueva["Calibre"] = v
            
        elif key in ["comercial", "comer.", "Comercial"]:
            nueva["comercial"] = v
            
        else:
            nueva[k] = v

    return nueva
