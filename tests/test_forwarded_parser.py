from app.core.email.forwarded_parser import extract_forwarded_headers, extract_original_recipients


def test_extract_forwarded_headers_from_mensaje_original_block() -> None:
    body = """
Hola,

-----Mensaje original-----
De: Alice <alice@example.com>
Para: Bob <bob@example.com>; carol@example.com
CC: dave@example.com
Asunto: Prueba
"""

    recipients = extract_forwarded_headers(body)

    assert recipients == {
        "from": "alice@example.com",
        "to_list": ["bob@example.com", "carol@example.com"],
        "cc_list": ["dave@example.com"],
    }


def test_extract_forwarded_headers_supports_multiline_fields_real_example() -> None:
    body = """
Texto previo.
-----Mensaje original-----
De: Juan Pérez <juan@example.com>
Para: Maria <maria@example.com>,
 Carlos <carlos@example.com>
CC: Equipo <equipo@example.com>;
 soporte@example.com

Asunto: Demo
"""

    recipients = extract_forwarded_headers(body)

    assert recipients == {
        "from": "juan@example.com",
        "to_list": ["maria@example.com", "carlos@example.com"],
        "cc_list": ["equipo@example.com", "soporte@example.com"],
    }


def test_extract_original_recipients_returns_empty_when_not_forwarded() -> None:
    body = "De: alice@example.com\nEste no es un bloque reenviado"

    recipients = extract_original_recipients(body)

    assert recipients == {}
