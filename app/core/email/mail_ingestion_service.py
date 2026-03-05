import base64
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.email.email_classifier import EmailClassifier
from app.core.email.forwarded_parser import extract_forwarded_headers, extract_real_sender
from app.config.mail_config import USER_EMAIL
from app.persistence.email_repository import EmailRepository
from app.services.email_entity_extractor import EmailEntityExtractor

logger = logging.getLogger(__name__)


class MailIngestionService:
    STATUS_NEW = "new"
    CATEGORY_PENDING = "pending"

    def __init__(
        self,
        gmail_client,
        db_connection: sqlite3.Connection,
        attachments_root: Path | None = None,
    ):
        self.gmail_client = gmail_client
        self.db_connection = db_connection
        self.email_repo = EmailRepository(db_connection)
        self.classifier = EmailClassifier(email_repo=self.email_repo)
        self.attachments_root = attachments_root or Path("attachments")
        self.attachments_root.mkdir(parents=True, exist_ok=True)

    def sync_unread_emails(self, max_results: int = 20) -> List[str]:
        self.ensure_table()

        processed_ids: List[str] = []
        unread_ids = self.gmail_client.list_unread_messages(max_results=max_results)

        for gmail_id in unread_ids:
            full_message = self.gmail_client.get_message(gmail_id, format="full")
            payload = full_message.get("payload", {})

            headers = self._extract_headers(payload)
            subject = headers["subject"]
            sender = headers["from"]
            body_text, body_html, has_attachments, attachments = self._extract_body_and_attachments(payload)
            real_sender = extract_real_sender(body_text, sender)

            original_from = headers["from"]
            original_to = headers["to"]
            original_cc = headers["cc"]
            original_reply_to = headers["reply_to"]
            if self._should_extract_forwarded_original_headers(subject, sender, body_text):
                forwarded = extract_forwarded_headers(body_text)
                if forwarded.get("from"):
                    original_from = forwarded["from"]
                    real_sender = forwarded["from"]
                if forwarded.get("to_list"):
                    original_to = ", ".join(forwarded["to_list"])
                if forwarded.get("cc_list"):
                    original_cc = ", ".join(forwarded["cc_list"])
                if forwarded.get("reply_to"):
                    original_reply_to = forwarded["reply_to"]

            received_at = self._convert_internal_date(full_message.get("internalDate"))
            email_type = self.classifier.classify(subject=subject, sender=sender, body_text=body_text)
            category = "marketing" if email_type in {"marketing", "subscription"} else "priority"
            entities_json = json.dumps(EmailEntityExtractor.extract_entities(subject, body_text), ensure_ascii=False)
            inserted = self._insert_email(
                gmail_id=gmail_id,
                thread_id=full_message.get("threadId"),
                subject=subject,
                sender=sender,
                real_sender=real_sender,
                original_from=original_from,
                original_to=original_to,
                original_cc=original_cc,
                original_reply_to=original_reply_to,
                received_at=received_at,
                body_text=body_text,
                body_html=body_html,
                has_attachments=has_attachments,
                raw_payload_json=json.dumps(payload, ensure_ascii=False),
                attachments_json=json.dumps(attachments, ensure_ascii=False),
                entities_json=entities_json,
                category=category,
                email_type=email_type,
            )

            if inserted:
                self._persist_attachments(gmail_id=gmail_id, attachments=attachments)
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
                real_sender TEXT,
                received_at TEXT,
                body_text TEXT,
                body_html TEXT,
                has_attachments INTEGER,
                raw_payload_json TEXT,
                attachments_json TEXT DEFAULT '[]',
                processed_at TEXT,
                status TEXT,
                category TEXT DEFAULT 'pending',
                type TEXT DEFAULT 'other',
                original_from TEXT DEFAULT '',
                original_to TEXT DEFAULT '',
                original_cc TEXT DEFAULT '',
                original_reply_to TEXT DEFAULT '',
                entities_json TEXT
            );
            """
        )
        self.db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS email_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT,
                local_path TEXT NOT NULL,
                size INTEGER,
                UNIQUE(gmail_id, filename, local_path)
            )
            """
        )
        columns = self.db_connection.execute("PRAGMA table_info(emails)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if "category" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN category TEXT DEFAULT 'pending'")
        if "type" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN type TEXT DEFAULT 'other'")
        if "original_from" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN original_from TEXT DEFAULT ''")
        if "real_sender" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN real_sender TEXT DEFAULT ''")
        if "original_to" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN original_to TEXT DEFAULT ''")
        if "original_cc" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN original_cc TEXT DEFAULT ''")
        if "original_reply_to" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN original_reply_to TEXT DEFAULT ''")
        if "attachments_json" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN attachments_json TEXT DEFAULT '[]'")
        if "entities_json" not in column_names:
            self.db_connection.execute("ALTER TABLE emails ADD COLUMN entities_json TEXT")

    def _insert_email(
        self,
        gmail_id: str,
        thread_id: Optional[str],
        subject: str,
        sender: str,
        real_sender: str,
        original_from: str,
        original_to: str,
        original_cc: str,
        original_reply_to: str,
        received_at: Optional[str],
        body_text: str,
        body_html: str,
        has_attachments: int,
        raw_payload_json: str,
        attachments_json: str,
        entities_json: str,
        category: str,
        email_type: str,
    ) -> bool:
        processed_at = datetime.now(timezone.utc).isoformat()
        cursor = self.db_connection.execute(
            """
            INSERT OR IGNORE INTO emails (
                gmail_id,
                thread_id,
                subject,
                sender,
                real_sender,
                received_at,
                body_text,
                body_html,
                has_attachments,
                raw_payload_json,
                attachments_json,
                processed_at,
                status,
                category,
                type,
                original_from,
                original_to,
                original_cc,
                original_reply_to,
                entities_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gmail_id,
                thread_id,
                subject,
                sender,
                real_sender,
                received_at,
                body_text,
                body_html,
                has_attachments,
                raw_payload_json,
                attachments_json,
                processed_at,
                self.STATUS_NEW,
                category,
                email_type,
                original_from,
                original_to,
                original_cc,
                original_reply_to,
                entities_json,
            ),
        )
        return cursor.rowcount > 0

    def _extract_headers(self, payload: Dict[str, Any]) -> Dict[str, str]:
        extracted = {"subject": "", "from": "", "to": "", "cc": "", "reply_to": ""}
        for header in payload.get("headers", []):
            name = header.get("name", "").lower()
            if name == "subject":
                extracted["subject"] = header.get("value", "")
            elif name == "from":
                extracted["from"] = header.get("value", "")
            elif name == "to":
                extracted["to"] = header.get("value", "")
            elif name == "cc":
                extracted["cc"] = header.get("value", "")
            elif name == "reply-to":
                extracted["reply_to"] = header.get("value", "")
        return extracted

    def _extract_body_and_attachments(self, payload: Dict[str, Any]) -> Tuple[str, str, int, List[Dict[str, Any]]]:
        body_text_parts: List[str] = []
        body_html_parts: List[str] = []
        attachments: List[Dict[str, Any]] = []

        def walk_part(part: Dict[str, Any]) -> None:
            filename = (part.get("filename") or "").strip()
            body = part.get("body", {}) or {}
            if filename:
                attachments.append(
                    {
                        "filename": filename,
                        "mimeType": part.get("mimeType", "application/octet-stream"),
                        "attachmentId": body.get("attachmentId", ""),
                        "partId": part.get("partId", ""),
                        "size": int(body.get("size") or 0),
                    }
                )

            mime_type = part.get("mimeType", "")
            body_data = body.get("data")
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
        return body_text, body_html, 1 if attachments else 0, attachments

    def _persist_attachments(self, gmail_id: str, attachments: List[Dict[str, Any]]) -> None:
        if not attachments:
            return
        safe_gmail_id = self._safe_path_segment(gmail_id)
        target_dir = self.attachments_root / safe_gmail_id
        target_dir.mkdir(parents=True, exist_ok=True)

        for attachment in attachments:
            filename = str(attachment.get("filename", ""))
            safe_filename = self._safe_filename(filename)
            attachment_id = str(attachment.get("attachmentId", "") or "")
            if not attachment_id:
                continue
            try:
                raw_bytes = self.gmail_client.get_attachment(gmail_id, attachment_id)
                if not raw_bytes:
                    raise ValueError("Respuesta sin datos del adjunto")
                target_path = target_dir / safe_filename
                target_path.write_bytes(raw_bytes)
                self.email_repo.save_attachment(
                    gmail_id=gmail_id,
                    filename=filename,
                    mime_type=str(attachment.get("mimeType", "application/octet-stream")),
                    local_path=str(target_path),
                    size=len(raw_bytes),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("No se pudo descargar/guardar adjunto. gmail_id=%s filename=%s error=%s", gmail_id, filename, exc)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        cleaned = filename.replace("\\", "_").replace("/", "_").strip()
        return cleaned or "attachment"

    @staticmethod
    def _safe_path_segment(segment: str) -> str:
        cleaned = segment.replace("\\", "_").replace("/", "_").strip()
        return cleaned or "email"

    @staticmethod
    def _is_forwarded(subject: str, body_text: str) -> bool:
        normalized_subject = (subject or "").strip().lower()
        if normalized_subject.startswith(("rv:", "fw:", "fwd:")):
            return True
        return "-----mensaje original-----" in (body_text or "").lower()

    def _should_extract_forwarded_original_headers(self, subject: str, sender: str, body_text: str) -> bool:
        if not self._is_forwarded(subject, body_text):
            return False
        sender_email = self._extract_email(sender)
        my_email = self._extract_email(USER_EMAIL)
        return bool(sender_email and my_email and sender_email == my_email)

    @staticmethod
    def _extract_email(raw_value: str) -> str:
        from email.utils import parseaddr

        return parseaddr(raw_value or "")[1].strip().lower()

    def _convert_internal_date(self, internal_date: Optional[str]) -> Optional[str]:
        if not internal_date:
            return None
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()

    def _decode_base64_url(self, data: Optional[str]) -> str:
        raw_bytes = self._decode_base64_url_to_bytes(data)
        if not raw_bytes:
            return ""
        return raw_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def _decode_base64_url_to_bytes(data: Optional[str]) -> bytes:
        if not data:
            return b""
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)
