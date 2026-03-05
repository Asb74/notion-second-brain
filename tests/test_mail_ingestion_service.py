import base64
import sqlite3
from pathlib import Path

from app.core.email.mail_ingestion_service import MailIngestionService


class _DummyGmailClient:
    def list_unread_messages(self, max_results: int = 20):
        return []

    def mark_as_read(self, gmail_id: str) -> None:
        return None


class _SyncClient(_DummyGmailClient):
    def list_unread_messages(self, max_results: int = 20):
        return ["gmail-1"]

    def get_message(self, gmail_id: str, format: str = "full"):
        body = base64.urlsafe_b64encode(
            "Intro\nDe: Real Sender <real.sender@example.com>\nMensaje".encode("utf-8")
        ).decode("ascii")
        return {
            "threadId": "thread-1",
            "internalDate": "1700000000000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Fwd: Demo"},
                    {"name": "From", "value": "Forwarder <forwarder@example.com>"},
                ],
                "parts": [{"mimeType": "text/plain", "body": {"data": body}}],
            },
        }


class _AttachmentClient(_DummyGmailClient):
    def __init__(self):
        self.attachment_requests: list[tuple[str, str]] = []

    def get_attachment(self, message_id: str, attachment_id: str):
        self.attachment_requests.append((message_id, attachment_id))
        return b"hola adjunto"


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


def test_extract_body_and_attachments_collects_attachment_metadata() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    service = MailIngestionService(gmail_client=_DummyGmailClient(), db_connection=conn)

    text_data = base64.urlsafe_b64encode("hola".encode("utf-8")).decode("ascii")
    body_text, body_html, has_attachments, attachments = service._extract_body_and_attachments(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": text_data}},
                {
                    "mimeType": "application/pdf",
                    "filename": "factura.pdf",
                    "body": {"attachmentId": "att-1"},
                },
            ],
        }
    )

    assert body_text == "hola"
    assert body_html == ""
    assert has_attachments == 1
    assert attachments == [
{"filename": "factura.pdf", "mimeType": "application/pdf", "attachmentId": "att-1", "partId": "", "size": 0}
    ]


def test_persist_attachments_downloads_and_registers(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    client = _AttachmentClient()
    service = MailIngestionService(gmail_client=client, db_connection=conn, attachments_root=tmp_path)
    service.ensure_table()

    service._persist_attachments(
        gmail_id="gmail-1",
        attachments=[
            {
                "filename": "reporte.txt",
                "mimeType": "text/plain",
                "attachmentId": "att-1",
            }
        ],
    )

    row = conn.execute(
        "SELECT gmail_id, filename, mime_type, local_path, size FROM email_attachments WHERE gmail_id = ?",
        ("gmail-1",),
    ).fetchone()

    assert row is not None
    assert row["filename"] == "reporte.txt"
    assert row["mime_type"] == "text/plain"
    assert Path(row["local_path"]).exists()
    assert Path(row["local_path"]).read_bytes() == b"hola adjunto"
    assert row["size"] == len(b"hola adjunto")
    assert client.attachment_requests == [("gmail-1", "att-1")]


def test_sync_unread_emails_persists_real_sender() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    service = MailIngestionService(gmail_client=_SyncClient(), db_connection=conn)

    processed = service.sync_unread_emails(max_results=5)

    row = conn.execute("SELECT sender, real_sender, entities_json FROM emails WHERE gmail_id = ?", ("gmail-1",)).fetchone()
    assert processed == ["gmail-1"]
    assert row is not None
    assert row["sender"] == "Forwarder <forwarder@example.com>"
    assert row["real_sender"] == "real.sender@example.com"


def test_sync_unread_emails_persists_entities_json() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    service = MailIngestionService(gmail_client=_SyncClient(), db_connection=conn)

    service.sync_unread_emails(max_results=5)

    row = conn.execute("SELECT entities_json FROM emails WHERE gmail_id = ?", ("gmail-1",)).fetchone()
    assert row is not None
    assert "pedido" in str(row["entities_json"] or "")
