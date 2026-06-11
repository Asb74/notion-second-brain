from app.services.knowledge_suggestion_service import suggest_knowledge_metadata


def test_suggests_invoice_email_metadata_with_normalized_tags():
    result = suggest_knowledge_metadata(
        title="Factura proveedor duplicada",
        content="Importe pendiente con vencimiento próximo",
        source="email",
        existing_tags=["email", ""],
    )

    assert result["area"] == "Trabajo"
    assert result["type"] == "Documento"
    assert result["topic"] == ""
    assert result["tags"] == ["Email", "Factura", "Proveedor", "Administración"]


def test_preserves_existing_metadata_and_limits_tags():
    result = suggest_knowledge_metadata(
        title="Reunión con seguimiento de router IP",
        content="Acta con acuerdo sobre conexión de cámara",
        source="manual",
        existing_area="Sansebas",
        existing_topic="Infraestructura",
        existing_type="Procedimiento",
        existing_tags=["uno", "dos", "tres", "cuatro"],
    )

    assert result["area"] == "Sansebas"
    assert result["topic"] == "Infraestructura"
    assert result["type"] == "Procedimiento"
    assert len(result["tags"]) == 6
    assert result["tags"][:4] == ["Uno", "Dos", "Tres", "Cuatro"]
