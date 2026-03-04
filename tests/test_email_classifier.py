from app.core.email.email_classifier import EmailClassifier


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
