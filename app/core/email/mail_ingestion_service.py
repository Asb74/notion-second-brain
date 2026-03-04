import base64
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


class MailIngestionService:
    STATUS_NEW = "new"
    CATEGORY_PENDING = "pending"

    MARKETING_KEYWORDS = (
        "oferta",
        "descuento",
        "promo",
        "publicidad",
        "newsletter",
    )

    def __init__(self, gmail_client, db_connection: sqlite3.Connection):
        self.gmail_client = gmail_client
        self.db_connection = db_connection

    def sync_unread_emails(self, max_results: int = 20) -> List[str]:
        self.ensure_table()

        processed_ids: List[str] = []
        unread_ids = self.gmail_client.list_unread_messages(max_results=max_results)

        for gmail_id in unread_ids:
            full_message = self.gmail_client.get_message(gmail_id, format="full")
            payload = full_message.get("payload", {})

            subject, sender = self._extract_headers(payload)
            body_text, body_html, has_attachments = self._extract_body_and_attachments(
                payload
            )

            received_at = self._convert_internal_date(full_message.get("internalDate"))
            category = self.classify_email(subject=subject, body_text=body_text)
            inserted = self._insert_email(
                gmail_id=gmail_id,
                thread_id=full_message.get("threadId"),
                subject=subject,
                sender=sender,
                received_at=received_at,
                body_text=body_text,
                body_html=body_html,
                has_attachments=has_attachments,
                raw_payload_json=json.dumps(payload, ensure_ascii=False),
                category=category,
            )

            if inserted:
                self.gmail_client.mark_as_read(gmail_id)
                processed_ids.append(gmail_id)

        self.db_connection.commit()
        return processed_ids

    def ensure_table(self) -> None:
        self.db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                gmail_id TEXT PRIMARY KEY,
                thread_id TEXT,
                subject TEXT,
                sender TEXT,
                received_at TEXT,
                body_text TEXT,
                body_html TEXT,
                has_attachments INTEGER,
                raw_payload_json TEXT,
                processed_at TEXT,
                status TEXT,
                category TEXT DEFAULT 'pending'
            );
            """
        )
        columns = self.db_connection.execute("PRAGMA table_info(emails)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if "category" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN category TEXT DEFAULT 'pending'")
        self.db_connection.commit()

    def _insert_email(
        self,
        gmail_id: str,
        thread_id: Optional[str],
        subject: str,
        sender: str,
        received_at: Optional[str],
        body_text: str,
        body_html: str,
        has_attachments: int,
        raw_payload_json: str,
        category: str,
    ) -> bool:
        processed_at = datetime.now(timezone.utc).isoformat()
        cursor = self.db_connection.execute(
            """
            INSERT OR IGNORE INTO emails (
                gmail_id,
                thread_id,
                subject,
                sender,
                received_at,
                body_text,
                body_html,
                has_attachments,
                raw_payload_json,
                processed_at,
                status,
                category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gmail_id,
                thread_id,
                subject,
                sender,
                received_at,
                body_text,
                body_html,
                has_attachments,
                raw_payload_json,
                processed_at,
                self.STATUS_NEW,
                category,
            ),
        )
        return cursor.rowcount > 0

    def classify_email(self, subject: str, body_text: str) -> str:
        normalized = f"{subject or ''} {body_text or ''}".lower()
        if any(keyword in normalized for keyword in self.MARKETING_KEYWORDS):
            return "marketing"
        return "priority"

    def _extract_headers(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        subject = ""
        sender = ""

        for header in payload.get("headers", []):
            name = header.get("name", "").lower()
            if name == "subject":
                subject = header.get("value", "")
            elif name == "from":
                sender = header.get("value", "")

        return subject, sender

    def _extract_body_and_attachments(
        self,
        payload: Dict[str, Any]
    ) -> Tuple[str, str, int]:
        body_text_parts: List[str] = []
        body_html_parts: List[str] = []
        has_attachments = 0

        def walk_part(part: Dict[str, Any]) -> None:
            nonlocal has_attachments

            filename = part.get("filename")
            if filename:
                has_attachments = 1

            mime_type = part.get("mimeType", "")
            body_data = part.get("body", {}).get("data")
            decoded = self._decode_base64_url(body_data)

            if mime_type == "text/plain" and decoded:
                body_text_parts.append(decoded)
            elif mime_type == "text/html" and decoded:
                body_html_parts.append(decoded)

            for child in part.get("parts", []):
                walk_part(child)

        walk_part(payload)

        body_text = "\n\n".join(body_text_parts).strip()
        body_html = "\n\n".join(body_html_parts).strip()
        return body_text, body_html, has_attachments

    def _convert_internal_date(self, internal_date: Optional[str]) -> Optional[str]:
        if not internal_date:
            return None

        timestamp_seconds = int(internal_date) / 1000
        return datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc).isoformat()

    def _decode_base64_url(self, data: Optional[str]) -> str:
        if not data:
            return ""

        padding = "=" * (-len(data) % 4)
        raw_bytes = base64.urlsafe_b64decode(data + padding)
        return raw_bytes.decode("utf-8", errors="replace")
