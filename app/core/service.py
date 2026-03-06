"""Application service layer coordinating domain, persistence and integrations."""

from __future__ import annotations

import logging
from datetime import datetime
from tkinter import messagebox

from app.core.hashing import compute_source_id
from app.core.models import Action, AppSettings, Note, NoteCreateRequest, NoteStatus
from app.core.outlook.outlook_service import OutlookService
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

    MASTER_TO_NOTION_PROP: dict[str, str] = {
        "Area": "prop_area",
        "Tipo": "prop_tipo",
        "Prioridad": "prop_prioridad",
        "Origen": "Origen",
    }

    def __init__(
        self,
        note_repo: NoteRepository,
        settings_repo: SettingsRepository,
        masters_repo: MastersRepository,
        actions_repo: ActionsRepository,
        outlook_service: OutlookService | None = None,
    ):
        self.note_repo = note_repo
        self.settings_repo = settings_repo
        self.masters_repo = masters_repo
        self.actions_repo = actions_repo
        self.outlook_service = outlook_service or OutlookService()
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
        return self.masters_repo.list_active(field_name)

    def list_masters(self, category: str):
        return self.masters_repo.list_all(category)

    def add_master(self, category: str, value: str) -> None:
        self.masters_repo.add_master(category, value)

    def deactivate_master(self, category: str, value: str) -> None:
        if self.masters_repo.is_locked(category, value):
            raise ValueError(f"'{value}' está bloqueado por el sistema y no puede desactivarse.")

        settings = self.get_settings()
        if not settings.notion_database_id.strip() or not settings.notion_token.strip():
            logger.warning("Desactivación sin validación Notion completa por configuración incompleta")
            self.masters_repo.deactivate_master(category, value)
            return

        notion_property = self._resolve_notion_property_name(category, settings)
        if not notion_property:
            self.masters_repo.deactivate_master(category, value)
            return

        client = NotionClient(settings.notion_token)
        open_count = client.count_open_pages_for_master(
            settings.notion_database_id,
            notion_property,
            value,
            settings.prop_estado,
            "Finalizado",
        )
        if open_count > 0:
            raise ValueError(
                f"No se puede desactivar '{value}' porque existe en {open_count} página(s) de Notion con Estado distinto de 'Finalizado'."
            )

        self.masters_repo.deactivate_master(category, value)

    def sync_schema_with_notion(self, settings: AppSettings | None = None) -> None:
        current = settings or self.get_settings()
        self._validate_notion_settings(current)
        if not current.notion_database_id.strip():
            raise NotionError("Debe crear la base Notion antes de sincronizar maestros.")

        client = NotionClient(current.notion_token)
        schema = client.get_database_schema(current.notion_database_id)
        properties = schema.get("properties", {})

        patch_properties: dict[str, dict] = {}
        for category in self.MASTER_TO_NOTION_PROP:
            notion_property = self._resolve_notion_property_name(category, current)
            if not notion_property or notion_property not in properties:
                logger.warning("Propiedad '%s' no encontrada en esquema Notion para categoría '%s'", notion_property, category)
                continue

            active_values = self.masters_repo.list_active(category)
            existing_options = (
                properties.get(notion_property, {})
                .get("select", {})
                .get("options", [])
            )
            color_by_name = {
                str(opt.get("name")): str(opt.get("color", "default"))
                for opt in existing_options
                if opt.get("name")
            }
            options = [{"name": value, "color": color_by_name.get(value, "default")} for value in active_values]
            patch_properties[notion_property] = {"select": {"options": options}}

        if patch_properties:
            client.patch_database_properties(current.notion_database_id, patch_properties)

    def _resolve_notion_property_name(self, category: str, settings: AppSettings) -> str | None:
        key = self.MASTER_TO_NOTION_PROP.get(category)
        if not key:
            return None
        if key.startswith("prop_"):
            return str(getattr(settings, key))
        return key

    def list_pending_actions(self, area: str | None = None) -> list[Action]:
        if area:
            return [a for a in self.actions_repo.get_actions_by_area(area) if a.status == "pendiente"]
        return self.actions_repo.get_pending_actions()

    def _generate_actions_summary(self, note_id: int) -> str:
        actions = self.actions_repo.get_actions_by_note(note_id)

        lines: list[str] = []
        for action in actions:
            desc = str(action.description).strip()
            if desc:
                lines.append(f"• {desc}")

        return "\n".join(lines)

    def _detect_email_type(self, note: Note) -> str:
        text = f"{note.title or ''} {note.raw_text or ''}".lower()

        if any(w in text for w in ["pedido", "orden", "confirmar pedido"]):
            return "pedido"

        if any(w in text for w in ["transporte", "camión", "logistica", "carga"]):
            return "logistica"

        if any(w in text for w in ["incidencia", "problema", "error", "reclamación"]):
            return "incidencia"

        if any(w in text for w in ["consulta", "información", "pregunta"]):
            return "informacion"

        return "general"

    def _build_email_template(self, tipo: str, acciones: str) -> str:
        templates = {
            "pedido": f"""Hola,

Hemos revisado tu pedido y realizado las gestiones necesarias.

Acciones realizadas:
{acciones}

Quedamos atentos a cualquier detalle adicional.

Un saludo""",
            "logistica": f"""Hola,

La gestión logística solicitada ha sido revisada.

Acciones realizadas:
{acciones}

Si necesitas cualquier ajuste adicional, quedamos pendientes.

Un saludo""",
            "incidencia": f"""Hola,

Hemos revisado la incidencia indicada.

Acciones realizadas:
{acciones}

Si necesitas ampliar información estaremos encantados de ayudarte.

Un saludo""",
            "informacion": f"""Hola,

Hemos revisado tu consulta.

Acciones realizadas:
{acciones}

Quedamos atentos a cualquier otra cuestión.

Un saludo""",
            "general": f"""Hola,

Hemos realizado las siguientes acciones sobre tu solicitud:

{acciones}

Quedamos atentos.

Un saludo""",
        }

        return templates.get(tipo, templates["general"])

    def _handle_note_completed(self, note_id: int) -> None:
        open_count = self.actions_repo.count_open_actions(note_id)
        if open_count > 0:
            return

        note = self.note_repo.get_note(note_id)
        if not note:
            return

        settings = self.get_settings()
        managed_email = settings.managed_email.strip().lower()

        self.note_repo.update_estado(note.id, "Finalizado")

        if note.source != "email_pasted" or not note.source_id.strip():
            return

        if note.email_replied == 1:
            return

        message = (
            "Has terminado todas las tareas asociadas a este email.\n"
            "¿Deseas preparar una respuesta?"
        )
        if not messagebox.askyesno("Email finalizado", message):
            return

        summary = self._generate_actions_summary(note_id)
        if not summary.strip():
            summary = "• Gestión completada."

        tipo = self._detect_email_type(note)
        body = self._build_email_template(tipo, summary)

        try:
            created = self.outlook_service.reply_all_with_body(
                note.source_id,
                body,
                exclude_email=managed_email,
            )
            if not created:
                return
            self.note_repo.set_email_replied(note.id)
            logger.info("Email reply prepared for note_id=%s", note.id)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo abrir respuesta automática para note_id=%s email_id=%s", note.id, note.source_id)

    def check_note_completion(self, note_id: int) -> None:
        self._handle_note_completed(note_id)

    def mark_actions_done(self, action_ids: list[int]) -> int:
        completed_note_ids: set[int] = set()

        for action_id in action_ids:
            action = self.actions_repo.get_action(action_id)
            if not action:
                continue

            self.actions_repo.mark_action_done(action_id)
            completed_note_ids.add(action.note_id)
            self._sync_action_done_with_notion(action)

        for note_id in completed_note_ids:
            self.check_note_completion(note_id)

        return len(completed_note_ids)

    def mark_action_done(self, action_id: int) -> None:
        self.mark_actions_done([action_id])

    def _sync_action_done_with_notion(self, action: Action) -> None:
        note = self.note_repo.get_note(action.note_id)
        if not note:
            return

        settings = self.get_settings()
        if not settings.notion_token.strip():
            logger.warning(
                "No se sincronizó acción id=%s con Notion por falta de token",
                action.id,
            )
            return

        try:
            client = NotionClient(settings.notion_token)
            if action.notion_page_id:
                client.update_page_status(action.notion_page_id, "Finalizado", settings.prop_estado)

            if (
                note.notion_page_id
                and note.tipo == "Nota"
                and note.source_id.strip()
                and settings.notion_database_id.strip()
                and client.count_open_tasks_by_fuente_id(
                    settings.notion_database_id,
                    note.source_id,
                    settings.prop_tipo,
                    settings.prop_estado,
                    "Finalizado",
                )
                == 0
            ):
                client.update_page_status(note.notion_page_id, "Finalizado", settings.prop_estado)
        except Exception:  # noqa: BLE001
            logger.exception(
                "No se pudo sincronizar estado en Notion para la acción id=%s (task_page=%s)",
                action.id,
                action.notion_page_id,
            )

    def mark_note_done(self, note_id: int) -> None:
        note = self.note_repo.get_note(note_id)
        if not note:
            return

        pending_tasks = self.actions_repo.pending_count_by_note(note_id)
        if pending_tasks > 0:
            raise ValueError("No se puede finalizar la nota porque tiene tareas pendientes.")

        self.note_repo.update_estado(note_id, "Finalizado")

        settings = self.get_settings()
        if not settings.notion_token.strip() or not note.notion_page_id:
            return

        try:
            client = NotionClient(settings.notion_token)
            client.update_page_status(note.notion_page_id, "Finalizado", settings.prop_estado)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo sincronizar estado en Notion para la nota id=%s", note_id)

    def _filter_actions(self, actions, source):
        if not isinstance(actions, list):
            return []

        # remove duplicates and clean text
        cleaned = []
        for action in actions:
            text = str(action).strip()
            if len(text) < 5:
                continue
            if text not in cleaned:
                cleaned.append(text)

        # detect urgent actions
        urgent_keywords = ["urgente", "hoy", "mañana", "antes de", "lo antes posible"]

        urgent = []
        normal = []

        for action in cleaned:
            lower = action.lower()
            if any(k in lower for k in urgent_keywords):
                urgent.append(action)
            else:
                normal.append(action)

        ordered = urgent + normal

        # limit actions for emails
        if source == "email_pasted":
            return ordered[:3]

        return ordered

    def create_note(self, req: NoteCreateRequest) -> tuple[int | None, str]:
        normalized = normalize_text(req.raw_text, req.source)
        source_id = req.email_id.strip() if req.source == "email_pasted" and req.email_id.strip() else compute_source_id(normalized, req.source)

        if self.note_repo.source_exists(source_id):
            return None, "Nota duplicada detectada, no se guardó nuevamente."

        title = req.title.strip() if req.title else ""
        if not title:
            title = normalized.split("\n", 1)[0][:120] or "Sin título"

        processed = process_text(normalized)
        final_tipo = req.tipo.strip() if req.tipo.strip() else processed.tipo_sugerido
        final_prioridad = req.prioridad.strip() if req.prioridad.strip() else processed.prioridad_sugerida
        filtered_actions = self._filter_actions(processed.acciones, req.source)
        acciones_text = "\n".join(filtered_actions)

        final_estado = req.estado
        if not acciones_text.strip():
            final_estado = "Finalizado"

        final_req = NoteCreateRequest(
            raw_text=req.raw_text,
            source=req.source,
            area=req.area,
            tipo=final_tipo,
            estado=final_estado,
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

        for accion in filtered_actions:
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

                if note.acciones.strip():
                    local_actions = [a for a in self.actions_repo.get_actions_by_note(note.id) if a.status == "pendiente"]
                    action_id_by_description: dict[str, list[int]] = {}
                    for local_action in sorted(local_actions, key=lambda item: item.id):
                        action_id_by_description.setdefault(local_action.description.strip(), []).append(local_action.id)

                    for action in [line.strip() for line in note.acciones.splitlines() if line.strip()]:
                        try:
                            task_page_id = client.create_task_from_action(settings, action, note)
                            candidates = action_id_by_description.get(action, [])
                            if candidates:
                                self.actions_repo.set_notion_page_id(candidates.pop(0), task_page_id)
                        except Exception:  # noqa: BLE001
                            logger.exception("Error creating action task for note id=%s", note.id)

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
