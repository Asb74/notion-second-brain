from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core.email.gmail_client import GmailClient


class AttachmentCache:
    def __init__(self, gmail_client: GmailClient, cache_dir: Path | None = None):
        self.gmail_client = gmail_client
        self.cache_dir = cache_dir or Path("data/attachments_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_downloaded(self, email_gmail_id: str, attachment_meta: dict[str, Any]) -> str:
        filename = str(attachment_meta.get("filename") or "attachment")
        safe_email_id = email_gmail_id.replace("/", "_").replace("\\", "_")
        safe_filename = filename.replace("/", "_").replace("\\", "_")
        target_dir = self.cache_dir / safe_email_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = (target_dir / safe_filename).resolve()
        if target_path.exists():
            return str(target_path)

        existing_local = str(attachment_meta.get("local_path") or "").strip()
        if existing_local and Path(existing_local).exists():
            shutil.copy2(existing_local, target_path)
            return str(target_path)

        attachment_id = str(attachment_meta.get("attachmentId") or "").strip()
        if not attachment_id:
            raise ValueError(f"El adjunto '{filename}' no tiene attachmentId ni ruta local.")

        raw = self.gmail_client.get_attachment(email_gmail_id, attachment_id)
        if not raw:
            raise ValueError(f"No se pudo descargar el adjunto '{filename}'")
        target_path.write_bytes(raw)
        return str(target_path)
