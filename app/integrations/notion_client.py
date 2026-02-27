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

    def validate_database_schema(self, settings: AppSettings) -> NotionSchemaValidation:
        url = f"https://api.notion.com/v1/databases/{settings.notion_database_id}"
        import requests
        resp = requests.get(url, headers=self._headers, timeout=15)
        if resp.status_code >= 400:
            return NotionSchemaValidation(False, f"No se pudo leer la base de datos de Notion: {resp.text}")

        data = resp.json()
        props = data.get("properties", {})
        expected = {
            settings.prop_title: "title",
            settings.prop_area: "select",
            settings.prop_tipo: "select",
            settings.prop_estado: "status",
            settings.prop_fecha: "date",
            settings.prop_prioridad: "select",
        }
        for prop_name, expected_type in expected.items():
            if prop_name not in props:
                return NotionSchemaValidation(False, f"Falta la propiedad '{prop_name}' en Notion.")
            found_type = props[prop_name].get("type")
            if found_type != expected_type:
                return NotionSchemaValidation(
                    False,
                    f"La propiedad '{prop_name}' debe ser tipo '{expected_type}', encontrado '{found_type}'.",
                )
        return NotionSchemaValidation(True, "Esquema válido")

    def create_page(self, settings: AppSettings, note: Note) -> str:
        if not settings.notion_database_id:
            raise NotionError("Falta configurar notion_database_id.")

        payload: dict[str, Any] = {
            "parent": {"database_id": settings.notion_database_id},
            "properties": {
                settings.prop_title: {"title": [{"text": {"content": note.title}}]},
                settings.prop_area: {"select": {"name": note.area}},
                settings.prop_tipo: {"select": {"name": note.tipo}},
                settings.prop_estado: {"status": {"name": note.estado}},
                settings.prop_fecha: {"date": {"start": note.fecha}},
                settings.prop_prioridad: {"select": {"name": note.prioridad}},
            },
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": note.raw_text[:1900]}}]
                    },
                }
            ],
        }

        import requests
        resp = requests.post("https://api.notion.com/v1/pages", headers=self._headers, json=payload, timeout=20)
        if resp.status_code >= 400:
            raise NotionError(f"Error creando página en Notion: {resp.text}")
        return resp.json()["id"]
