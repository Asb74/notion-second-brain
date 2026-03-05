from app.services.email_entity_extractor import EmailEntityExtractor


def test_extract_entities_finds_required_fields() -> None:
    entities = EmailEntityExtractor.extract_entities(
        subject="Revisar pedido 123456",
        body="Cliente: Acme\nProducto: Tornillo\nContacto: juan.perez@example.com\nPor favor confirmar recepción.",
    )

    assert entities["pedido"] == "123456"
    assert entities["cliente"] == "Acme"
    assert entities["producto"] == "Tornillo"
    assert entities["persona"] in {"Juan Perez", "juan.perez@example.com"}
    assert entities["email_persona"] == "juan.perez@example.com"
    assert entities["accion"] == "revisar"


def test_extract_entities_returns_empty_when_not_found() -> None:
    entities = EmailEntityExtractor.extract_entities(subject="Hola", body="Sin datos")

    assert entities == {
        "pedido": "",
        "cliente": "",
        "producto": "",
        "persona": "",
        "email_persona": "",
        "accion": "",
    }


def test_extract_entities_uses_action_word_boundaries() -> None:
    entities = EmailEntityExtractor.extract_entities(
        subject="Información de la conversación",
        body="Texto de prueba sin acciones explícitas.",
    )

    assert entities["accion"] == ""
