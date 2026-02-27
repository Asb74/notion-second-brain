"""Application service orchestrating note creation and sync."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.core.hashing import compute_source_id
from app.core.models import AppSettings, Note, NoteCreateRequest, NoteStatus
from app.core.normalizer import normalize_text
from app.integrations.notion_client import NotionClient, NotionError
from app.persistence.repositories import NoteRepository, SettingsRepository

logger = logging.getLogger(__name__)


class NoteService:
    """Facade over repositories and integrations."""

    def __init__(
        self,
        note_repo: NoteRepository,
        settings_repo: SettingsRepository,
    ) -> None:
        self.note_repo = note_repo
        self.settings_repo = settings_repo

    def get_settings(self) -> AppSettings:
        return self.settings_repo.load()

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_repo.save(settings)

    def create_note(self, req: NoteCreateRequest) -> tuple[Optional[int], str]:
        normalized = normalize_text(req.raw_text, req.source)
        source_id = compute_source_id(normalized, req.source)
        if self.note_repo.source_exists(source_id):
            return None, "Duplicado detectado: esta nota ya existe localmente."

        title = req.title.strip() or self._autogenerate_title(normalized)
        req.title = title
        note_id = self.note_repo.create_note(req, source_id, datetime.utcnow().isoformat(timespec="seconds"), NoteStatus.PENDING)
        logger.info("Nota creada id=%s source_id=%s", note_id, source_id)
        return note_id, "Nota guardada localmente."

    def list_notes(self) -> list[Note]:
        return self.note_repo.list_notes()

    def sync_pending(self) -> tuple[int, int]:
        settings = self.get_settings()
        self._validate_notion_settings(settings)
        client = NotionClient(settings.notion_token)
        schema = client.validate_database_schema(settings)
        if not schema.ok:
            raise NotionError(schema.message)

        sent = 0
        failed = 0
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        for note in self.note_repo.list_retryable(now_iso):
            try:
                page_id = client.create_page(settings, note)
                self.note_repo.mark_sent(note.id, page_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.exception("Error sync note id=%s", note.id)
                self.note_repo.mark_error(note.id, str(exc), settings.retry_delay_seconds)
        return sent, failed

    @staticmethod
    def _autogenerate_title(normalized_text: str) -> str:
        first_line = normalized_text.split("\n", 1)[0]
        return (first_line[:80] or "Nota sin título").strip()

    @staticmethod
    def _validate_notion_settings(settings: AppSettings) -> None:
        if not settings.notion_token:
            raise NotionError("Falta configurar Notion token.")
        if not settings.notion_database_id:
            raise NotionError("Falta configurar Notion database_id.")
