"""Application service layer coordinating domain, persistence and integrations."""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.hashing import compute_source_id
from app.core.models import Action, AppSettings, Note, NoteCreateRequest, NoteStatus
from app.core.normalizer import normalize_text
from app.core.processor import process_text
from app.integrations.notion_client import NotionClient, NotionError
from app.integrations.notion_database_manager import (
    create_database,
    load_notion_config,
    validate_database_schema,
)
from app.persistence.masters_repository import MastersRepository
from app.persistence.repositories import ActionsRepository, NoteRepository, SettingsRepository

logger = logging.getLogger(__name__)


class NoteService:
    """Use-case layer for notes and synchronization flow."""

    def __init__(
        self,
        note_repo: NoteRepository,
        settings_repo: SettingsRepository,
        masters_repo: MastersRepository,
        actions_repo: ActionsRepository,
    ):
        self.note_repo = note_repo
        self.settings_repo = settings_repo
        self.masters_repo = masters_repo
        self.actions_repo = actions_repo
        self.masters_repo.ensure_default_values()

    def get_settings(self) -> AppSettings:
        return self.settings_repo.load()

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_repo.save(settings)

    def get_setting(self, key: str) -> str | None:
        return self.settings_repo.get_setting(key)

    def list_notes(self, limit: int = 200) -> list[Note]:
        return self.note_repo.list_notes(limit)

    def get_master_values(self, field_name: str) -> list[str]:
        return self.masters_repo.list_values(field_name)

    def list_pending_actions(self, area: str | None = None) -> list[Action]:
        if area:
            return [a for a in self.actions_repo.get_actions_by_area(area) if a.status == "pendiente"]
        return self.actions_repo.get_pending_actions()

    def mark_action_done(self, action_id: int) -> None:
        self.actions_repo.mark_action_done(action_id)

    def create_note(self, req: NoteCreateRequest) -> tuple[int | None, str]:
        normalized = normalize_text(req.raw_text, req.source)
        source_id = compute_source_id(normalized, req.source)

        if self.note_repo.source_exists(source_id):
            return None, "Nota duplicada detectada, no se guardó nuevamente."

        title = req.title.strip() if req.title else ""
        if not title:
            title = normalized.split("\n", 1)[0][:120] or "Sin título"

        processed = process_text(normalized)
        final_tipo = req.tipo.strip() if req.tipo.strip() else processed.tipo_sugerido
        final_prioridad = req.prioridad.strip() if req.prioridad.strip() else processed.prioridad_sugerida
        acciones_text = ""
        if isinstance(processed.acciones, list):
            acciones_text = "\n".join(str(accion).strip() for accion in processed.acciones if str(accion).strip())
        else:
            acciones_text = str(processed.acciones or "").strip()

        final_req = NoteCreateRequest(
            raw_text=req.raw_text,
            source=req.source,
            area=req.area,
            tipo=final_tipo,
            estado=req.estado,
            prioridad=final_prioridad,
            fecha=req.fecha,
            title=title,
            resumen=processed.resumen,
            acciones=acciones_text,
        )
        note_id = self.note_repo.create_note(
            final_req,
            source_id=source_id,
            created_at=AppSettings.now_iso(),
            status=NoteStatus.PENDING,
        )

        if isinstance(processed.acciones, list):
            for accion in processed.acciones:
                try:
                    action_description = str(accion).strip()
                    if not action_description:
                        continue
                    self.actions_repo.create_action(
                        note_id=note_id,
                        description=action_description,
                        area=final_req.area,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("No se pudo guardar la acción para la nota id=%s", note_id)

        return note_id, "Nota guardada localmente."

    def _validate_notion_settings(self, settings: AppSettings) -> None:
        if not settings.notion_token.strip():
            raise NotionError("Falta Notion token en Configuración.")

        required_properties = [
            settings.prop_title,
            settings.prop_area,
            settings.prop_tipo,
            settings.prop_estado,
            settings.prop_fecha,
            settings.prop_prioridad,
        ]
        if any(not prop.strip() for prop in required_properties):
            raise NotionError("Las propiedades de Notion no pueden estar vacías.")

    def sync_pending(self) -> tuple[int, int]:
        settings = self.get_settings()

        if not settings.notion_database_id.strip():
            raise NotionError("Debe crear la base Notion antes de sincronizar.")

        if not settings.notion_token.strip():
            raise NotionError("Falta Notion token en Configuración.")

        self._validate_notion_settings(settings)

        client = NotionClient(settings.notion_token)

        schema = client.validate_database_schema(
            settings.notion_database_id,
            settings,
        )
        if not schema.ok:
            raise NotionError(schema.message)

        sent = 0
        failed = 0

        now_iso = datetime.utcnow().isoformat(timespec="seconds")

        for note in self.note_repo.list_retryable(now_iso):
            try:
                page_id = client.create_page(
                    settings.notion_database_id,
                    settings,
                    note,
                )
                self.note_repo.mark_sent(note.id, page_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.exception("Error sync note id=%s", note.id)
                self.note_repo.mark_error(
                    note.id,
                    str(exc),
                    settings.retry_delay_seconds,
                )

        return sent, failed

    def create_notion_database_from_config(self) -> str:
        token, page_id = load_notion_config()
        database_id = create_database(token, page_id)
        validate_database_schema(token, database_id)
        self.settings_repo.set_setting("notion_database_id", database_id)
        self.settings_repo.set_setting("notion_token", token)
        return database_id
