from app.utils.anecoop_order_extractor import extraer_pedido_desde_pdf


def test_extraer_pedido_desde_pdf_anecoop_extrae_cabecera_y_linea() -> None:
    texto = """
    Anecoop ORDEN DE PEDIDO
    CLIENTE: EDEKA Pos. Cliente 1209303034
    F. Carga Sabado 28/03/2026 Plataforma: PX-1
    Nº Pedido: 25/111599/1 P.Carga: LORA DEL RIO
    De: Juan Perez AE-mail: comercial@demo.com
    Lin.
    4 EuroChepX60
    Simple
    Total Cajas: 240
    (*) Naranja Navel Lane Late
    Calibre: 2/3
    Observaciones
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    assert lineas[0]["NumeroPedido"] == "25/111599/1"
    assert lineas[0]["Cliente"] == "EDEKA"
    assert lineas[0]["FechaSalida"] == "28/03/2026"
    assert lineas[0]["PuntoCarga"] == "LORA DEL RIO"
    assert lineas[0]["Linea"] == "4"
    assert lineas[0]["Cantidad"] == "4"
    assert lineas[0]["TipoPalet"] == "EuroChepX60"
    assert lineas[0]["CajasTotales"] == "240"
    assert lineas[0]["Mercancia"] == "Naranja Navel Lane Late"
    assert lineas[0]["Calibre"] == "2/3"


def test_extraer_pedido_desde_pdf_sin_numero_no_devuelve_pedido() -> None:
    texto = "Anecoop ORDEN DE PEDIDO CLIENTE: EDEKA F. Carga Sabado 28/03/2026"
    assert extraer_pedido_desde_pdf(texto) == []


def test_extraer_pedido_desde_pdf_lineas_sin_observaciones_y_mercancia_multilinea() -> (
    None
):
    texto = """
    Anecoop ORDEN DE PEDIDO
    CLIENTE: EDEKA Pos. Cliente 1209303034
    F. Carga Sabado 28/03/2026 Plataforma: PX-1
    Nº Pedido: 25/111599/1 P.Carga: LORA DEL RIO
    De: Juan Perez AE-mail: comercial@demo.com
    Lin.
    2 EuroChepX36
    Total Cajas: 72
    (*) Naranja Navel
    Lane Late
    Calibre: 3/4
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    assert lineas[0]["Cantidad"] == "2"
    assert lineas[0]["TipoPalet"] == "EuroChepX36"
    assert lineas[0]["Mercancia"] == "Naranja Navel Lane Late"


def test_extraer_pedido_desde_pdf_acepta_valores_no_catalogados_y_campos_genericos() -> (
    None
):
    texto = """
    ORDEN DE PEDIDO
    CLIENTE: Cliente Cooperativa Norte Pos. Cliente X-42
    F. Carga Lunes 01/04/2026 Plataforma: Nave Experimental 7
    Nº Pedido: ABC-77/2026 P.Carga: Muelle Variable Sur
    Lin.
    8 FormatoInventado-Z9
    Caja telescópica 40x30 6 kg encajado especial
    Total Ud.Venta: 112
    (*) Fruta Azul Variedad Desconocida
    Calibre: Del 10 al 14
    Cat. EXTRA
    Marca: Marca Libre 2030
    Lote: LT-XYZ-9
    Observaciones: servir con control adicional
    Observaciones
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    assert lineas[0]["NumeroPedido"] == "ABC-77/2026"
    assert lineas[0]["Cliente"] == "Cliente Cooperativa Norte"
    assert lineas[0]["Cantidad"] == "8"
    assert lineas[0]["TipoPalet"] == "FormatoInventado-Z9"
    assert lineas[0]["CajasTotales"] == "112"
    assert lineas[0]["Confeccion"] == "Caja telescópica 40x30 6 kg encajado especial"
    assert lineas[0]["Mercancia"] == "Fruta Azul Variedad Desconocida"
    assert lineas[0]["Calibre"] == "Del 10 al 14"
    assert lineas[0]["Categoria"] == "CAT.EXTRA"
    assert lineas[0]["Marca"] == "Marca Libre 2030"
    assert lineas[0]["Lote"] == "LT-XYZ-9"


def test_extraer_pedido_desde_pdf_conserva_linea_parcial_con_warning(caplog) -> None:
    texto = """
    ORDEN DE PEDIDO
    Nº Pedido: 26/000001/1
    Lin.
    1 PaletVariable
    Total Cajas: 10
    Observaciones
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    assert lineas[0]["Linea"] == "1"
    assert lineas[0]["Cantidad"] == "1"
    assert lineas[0]["TipoPalet"] == "PaletVariable"
    assert lineas[0]["CajasTotales"] == "10"
    assert lineas[0]["Mercancia"] == ""
    assert "Warnings" in lineas[0]
    assert "campos no detectados" in caplog.text.lower()


def test_extraer_pedido_desde_pdf_sin_numero_con_linea_identificable_no_descarta() -> (
    None
):
    texto = """
    ORDEN DE PEDIDO
    CLIENTE: Cliente Sin Numero Pos. Cliente 1
    Lin.
    3 ContenedorLibre
    Total Cajas: 30
    (*) Producto Sin Numero
    Observaciones
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    assert lineas[0]["NumeroPedido"] == ""
    assert lineas[0]["Cliente"] == "Cliente Sin Numero"
    assert lineas[0]["Mercancia"] == "Producto Sin Numero"


def test_extraer_pedido_desde_pdf_lidl_compacto_total_cajas_no_crea_segunda_linea() -> None:
    texto = """
    ORDEN DE PEDIDO
    Nº Pedido: 25/154926/1
    CLIENTE: LIDL STIFT HORT
    F. Carga: Lunes 08/06/2026
    Plataforma: LIDL (ALEMANIA)
    P. Carga: LORA DEL RIO SIN TRANSBORDC DIRECTO A
    Lin. Cantidad Mercancia y Confección Calibre Ct Marca Precio Orientativo N°Lote/Ean Ref.Cliente
    19 Euro.Retor.X36 Naranja Blanca Valencia Late Del 3/4 al 4/5 | PZ/UV:LIDL Precio Mercado / 80142
    Simple 10xMALLA 2Kg.60x40x24 CART Caja:LIDL

    4 | Total Cajas: 684 (MALLA) Etiq Caja:LIDL

    Origen: ESPANA
    Observaciones: !dioma: aleman // 8 -10 Pz
    IAN: 80142
    Ean UV 20241681
    Malla roja // Caja verde
    BRIX 11 // 40% ZUMO
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    linea = lineas[0]
    assert linea["NumeroPedido"] == "25/154926/1"
    assert linea["Cliente"] == "LIDL STIFT HORT"
    assert linea["FechaSalida"] == "Lunes 08/06/2026"
    assert linea["Plataforma"] == "LIDL"
    assert linea["Pais"] == "ALEMANIA"
    assert linea["PuntoCarga"] == "LORA DEL RIO"
    assert linea["Estado"] == "Nuevo"
    assert linea["Linea"] == "1"
    assert linea["Cantidad"] == "19"
    assert linea["TipoPalet"] == "Euro.Retor. Simple"
    assert linea["CajasTotales"] == "684"
    assert linea["CP"] == "36"
    assert linea["NombreCaja"] == "LIDL"
    assert linea["Mercancia"] == "Naranja Blanca Valencia Late"
    assert linea["Confeccion"] == "10xMALLA 2Kg.60x40x24 CART"
    assert linea["Calibre"] == "Del 3/4 al 4/5"
    assert linea["Categoria"] == "I"
    assert linea["Marca"] == "LIDL"
    assert linea["PO"] == "Precio Mercado"
    assert linea["Lote"] == "80142"
    assert linea["Observaciones"] == (
        "!dioma: aleman // 8 -10 Pz // IAN: 80142 // Ean UV 20241681 // "
        "Malla roja // Caja verde // BRIX 11 // 40% ZUMO"
    )
