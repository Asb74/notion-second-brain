from app.core.email.email_classifier import EmailClassifier, is_internal_email, is_user_email


def test_classifier_detects_order_rule() -> None:
    classifier = EmailClassifier(email_repo=None)

    result = classifier.classify(
        subject="Pedido 101234 pendiente de entrega",
        sender="Proveedor <ventas@externo.com>",
        body_text="",
    )

    assert result == "order"


def test_classifier_prioritizes_internal_priority_hints() -> None:
    classifier = EmailClassifier(email_repo=None)

    result = classifier.classify(
        subject="Incidencia urgente en transporte",
        sender="Operaciones <ops@sansebas.es>",
        body_text="",
    )

    assert result == "priority"


def test_user_and_internal_email_helpers() -> None:
    profile = {
        "email_principal": "ana@empresa.com",
        "dominio": "empresa.com",
        "alias": ["ventas@empresa.com"],
    }
    assert is_user_email("Ana <ana@empresa.com>", profile)
    assert is_user_email("ventas@empresa.com", profile)
    assert not is_user_email("proveedor@externo.com", profile)
    assert is_internal_email("equipo@empresa.com", profile)
    assert not is_internal_email("equipo@otro.com", profile)
