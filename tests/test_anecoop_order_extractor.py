from app.utils.anecoop_order_extractor import extraer_pedido_desde_pdf


def test_extraer_pedido_desde_pdf_anecoop_extrae_cabecera_y_linea() -> None:
    texto = """
    Anecoop ORDEN DE PEDIDO
    CLIENTE: EDEKA Pos. Cliente 1209303034
    F. Carga Sabado 28/03/2026 Plataforma: PX-1
    Nº Pedido: 25/111599/1 P.Carga: LORA DEL RIO
    De: Juan Perez AE-mail: comercial@demo.com
    1 10 300 NARANJA CAL 3
    """

    lineas = extraer_pedido_desde_pdf(texto)

    assert len(lineas) == 1
    assert lineas[0]["NumeroPedido"] == "25/111599/1"
    assert lineas[0]["Cliente"] == "EDEKA"
    assert lineas[0]["FechaSalida"] == "28/03/2026"
    assert lineas[0]["PuntoCarga"] == "LORA DEL RIO"
    assert lineas[0]["Cantidad"] == "10"
    assert lineas[0]["CajasTotales"] == "300"
    assert lineas[0]["Mercancia"] == "NARANJA"


def test_extraer_pedido_desde_pdf_sin_numero_no_devuelve_pedido() -> None:
    texto = "Anecoop ORDEN DE PEDIDO CLIENTE: EDEKA F. Carga Sabado 28/03/2026"
    assert extraer_pedido_desde_pdf(texto) == []

