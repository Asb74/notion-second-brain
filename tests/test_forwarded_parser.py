from app.core.email.forwarded_parser import extract_original_recipients


def test_extract_original_recipients_from_mensaje_original_block() -> None:
    body = """
Hola,

-----Mensaje original-----
De: Alice <alice@example.com>
Para: Bob <bob@example.com>; carol@example.com
CC: dave@example.com
Asunto: Prueba
"""

    recipients = extract_original_recipients(body)

    assert recipients == {
        "from": "alice@example.com",
        "to": "bob@example.com, carol@example.com",
        "cc": "dave@example.com",
    }


def test_extract_original_recipients_returns_empty_when_not_forwarded() -> None:
    body = "De: alice@example.com\nEste no es un bloque reenviado"

    recipients = extract_original_recipients(body)

    assert recipients == {}
