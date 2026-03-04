import sqlite3

from app.core.email.mail_ingestion_service import MailIngestionService


class _DummyGmailClient:
    def list_unread_messages(self, max_results: int = 20):
        return []


def test_extract_headers_includes_reply_to_to_and_cc() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    service = MailIngestionService(gmail_client=_DummyGmailClient(), db_connection=conn)

    headers = service._extract_headers(
        {
            "headers": [
                {"name": "Subject", "value": "Hola"},
                {"name": "From", "value": "Cliente <cliente@mail.com>"},
                {"name": "To", "value": "Equipo <team@mail.com>"},
                {"name": "Cc", "value": "Boss <boss@mail.com>"},
                {"name": "Reply-To", "value": "Soporte <soporte@mail.com>"},
            ]
        }
    )

    assert headers == {
        "subject": "Hola",
        "from": "Cliente <cliente@mail.com>",
        "to": "Equipo <team@mail.com>",
        "cc": "Boss <boss@mail.com>",
        "reply_to": "Soporte <soporte@mail.com>",
    }
