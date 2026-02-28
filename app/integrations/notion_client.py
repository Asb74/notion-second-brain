"""Minimal Notion API client using requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.models import AppSettings, Note

NOTION_VERSION = "2022-06-28"


class NotionError(RuntimeError):
    """Domain error for Notion integration failures."""


@dataclass(slots=True)
class NotionSchemaValidation:
    ok: bool
    message: str


class NotionClient:
    """HTTP client for Notion database operations."""

    def __init__(self, token: str):
        self.token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def validate_database_schema(
        self,
        database_id: str,
        settings: AppSettings,
    ) -> NotionSchemaValidation:
        import requests

        url = f"https://api.notion.com/v1/databases/{database_id}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=15)
        except requests.RequestException as exc:
            return NotionSchemaValidation(False, f"Error de red al validar base de Notion: {exc}")

        if resp.status_code >= 400:
            return NotionSchemaValidation(
                False,
                f"No se pudo leer la base de datos de Notion: {resp.text}",
            )

        data = resp.json()
        props = data.get("properties", {})

        expected = {
            settings.prop_title: "title",
            settings.prop_area: "select",
            settings.prop_tipo: "select",
            settings.prop_estado: "select",
            settings.prop_fecha: "date",
            settings.prop_prioridad: "select",
        }

        for prop_name, expected_type in expected.items():
            if prop_name not in props:
                return NotionSchemaValidation(
                    False,
                    f"Falta la propiedad '{prop_name}' en Notion.",
                )

            found_type = props[prop_name].get("type")
            if found_type != expected_type:
                return NotionSchemaValidation(
                    False,
                    f"La propiedad '{prop_name}' debe ser tipo '{expected_type}', encontrado '{found_type}'.",
                )

        return NotionSchemaValidation(True, "Esquema válido")


    def count_open_pages_for_master(
        self,
        database_id: str,
        category_property: str,
        value: str,
        estado_property: str,
        estado_finalizado: str = "Finalizado",
    ) -> int:
        import requests

        count = 0
        has_more = True
        next_cursor: str | None = None

        while has_more:
            payload: dict[str, Any] = {
                "page_size": 100,
                "filter": {
                    "and": [
                        {
                            "property": category_property,
                            "select": {"equals": value},
                        },
                        {
                            "property": estado_property,
                            "select": {"does_not_equal": estado_finalizado},
                        },
                    ]
                },
            }
            if next_cursor:
                payload["start_cursor"] = next_cursor

            try:
                resp = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=self._headers,
                    json=payload,
                    timeout=20,
                )
            except requests.RequestException as exc:
                raise NotionError(f"Error de red consultando uso de maestro en Notion: {exc}") from None

            if resp.status_code >= 400:
                raise NotionError(f"Error consultando uso de maestro en Notion: {resp.text}")

            data = resp.json()
            count += len(data.get("results", []))
            has_more = bool(data.get("has_more", False))
            next_cursor = data.get("next_cursor")

        return count

    def get_database_schema(self, database_id: str) -> dict[str, Any]:
        import requests

        url = f"https://api.notion.com/v1/databases/{database_id}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=15)
        except requests.RequestException as exc:
            raise NotionError(f"Error de red leyendo esquema de Notion: {exc}") from None

        if resp.status_code >= 400:
            raise NotionError(f"No se pudo leer el esquema de Notion: {resp.text}")

        return resp.json()

    def patch_database_properties(self, database_id: str, properties: dict[str, Any]) -> None:
        import requests

        payload = {"properties": properties}
        try:
            resp = requests.patch(
                f"https://api.notion.com/v1/databases/{database_id}",
                headers=self._headers,
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise NotionError(f"Error de red actualizando esquema de Notion: {exc}") from None

        if resp.status_code >= 400:
            raise NotionError(f"Error actualizando esquema de Notion: {resp.text}")


    def update_page_status(self, page_id: str, new_status: str) -> None:
        import requests

        payload = {
            "properties": {
                "Estado": {
                    "select": {"name": new_status}
                }
            }
        }

        try:
            resp = requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=self._headers,
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise NotionError(f"Error de red actualizando estado de página en Notion: {exc}") from None

        if resp.status_code >= 400:
            raise NotionError(f"Error actualizando estado de página en Notion: {resp.text}")

    def create_page(
        self,
        database_id: str,
        settings: AppSettings,
        note: Note,
    ) -> str:
        import requests

        title_content = ((note.title or "").strip() or "Sin título")[:200]

        children = []
        if note.resumen.strip():
            children.append(
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Resumen"}}]},
                }
            )
            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": note.resumen[:1900]}}]
                    },
                }
            )

        if note.acciones.strip():
            children.append(
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Acciones"}}]},
                }
            )
            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": note.acciones[:1900]}}]
                    },
                }
            )

        children.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Texto original"}}]},
            }
        )
        children.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": note.raw_text[:1900]},
                        }
                    ]
                },
            }
        )

        payload: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": {
                settings.prop_title: {
                    "title": [{"text": {"content": title_content}}]
                },
                settings.prop_area: {"select": {"name": note.area}},
                settings.prop_tipo: {"select": {"name": note.tipo}},
                settings.prop_estado: {"select": {"name": note.estado}},
                settings.prop_fecha: {"date": {"start": note.fecha}},
                settings.prop_prioridad: {
                    "select": {"name": note.prioridad}
                },
            },
            "children": children,
        }

        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=self._headers,
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise NotionError(f"Error de red creando página en Notion: {exc}") from None

        if resp.status_code >= 400:
            raise NotionError(f"Error creando página en Notion: {resp.text}")

        return resp.json()["id"]

    def create_task_from_action(
        self,
        settings: AppSettings,
        action_text: str,
        parent_note: Note,
    ) -> str:
        import requests

        activity = (action_text or "").strip()[:200] or "Sin actividad"
        raw_action = (action_text or "").strip()

        payload: dict[str, Any] = {
            "parent": {"database_id": settings.notion_database_id},
            "properties": {
                settings.prop_title: {
                    "title": [{"text": {"content": activity}}]
                },
                settings.prop_tipo: {"select": {"name": "Tarea"}},
                settings.prop_estado: {"select": {"name": "Pendiente"}},
                settings.prop_area: {"select": {"name": parent_note.area}},
                settings.prop_fecha: {"date": {"start": parent_note.fecha}},
                "Origen": {"select": {"name": "Sistema"}},
                "Fuente_ID": {
                    "rich_text": [{"type": "text", "text": {"content": str(parent_note.id)}}]
                },
                "Raw": {
                    "rich_text": [{"type": "text", "text": {"content": raw_action[:1900]}}]
                },
            },
        }

        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=self._headers,
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise NotionError(f"Error de red creando tarea en Notion: {exc}") from None

        if resp.status_code >= 400:
            raise NotionError(f"Error creando tarea en Notion: {resp.text}")

        return resp.json()["id"]
