"""Notion database provisioning helpers."""

from __future__ import annotations

from pathlib import Path

import requests


NOTION_VERSION = "2022-06-28"
DATABASE_NAME = "Sistema Antonio – Bitácora Automatizada"
EXPECTED_SCHEMA: dict[str, str] = {
    "Actividad": "title",
    "Area": "select",
    "Tipo": "select",
    "Estado": "select",
    "Prioridad": "select",
    "Fecha": "date",
    "Origen": "select",
    "Fuente_ID": "rich_text",
    "Resumen": "rich_text",
    "Acciones": "rich_text",
    "Raw": "rich_text",
}


class NotionDatabaseError(RuntimeError):
    """Raised when creating or validating a Notion database fails."""


def load_notion_config(config_path: Path | None = None) -> tuple[str, str]:
    """Read TOKEN and PAGE_ID from app/notion_config.txt."""
    path = config_path or Path(__file__).resolve().parents[1] / "notion_config.txt"
    if not path.exists():
        raise NotionDatabaseError(f"No se encontró archivo de configuración: {path}")

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()

    token = values.get("TOKEN", "")
    page_id = values.get("PAGE_ID", "")
    if not token:
        raise NotionDatabaseError("TOKEN no configurado en app/notion_config.txt")
    if not page_id:
        raise NotionDatabaseError("PAGE_ID no configurado en app/notion_config.txt")
    return token, page_id


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _raise_http_error(status_code: int, body: str) -> None:
    if status_code == 401:
        raise NotionDatabaseError("401 Unauthorized: verifica TOKEN y permisos de integración en Notion.")
    if status_code == 404:
        raise NotionDatabaseError("404 Not Found: PAGE_ID o DATABASE_ID inválido, o sin acceso de la integración.")
    if status_code == 400:
        raise NotionDatabaseError(f"400 Bad Request: payload inválido para Notion. Detalle: {body}")
    raise NotionDatabaseError(f"Error Notion ({status_code}): {body}")


def create_database(token: str, page_id: str) -> str:
    """Create the definitive Notion database and return its ID."""
    payload = {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": DATABASE_NAME}}],
        "properties": {
            "Actividad": {"title": {}},
            "Area": {"select": {}},
            "Tipo": {"select": {}},
            "Estado": {"select": {}},
            "Prioridad": {"select": {}},
            "Fecha": {"date": {}},
            "Origen": {"select": {}},
            "Fuente_ID": {"rich_text": {}},
            "Resumen": {"rich_text": {}},
            "Acciones": {"rich_text": {}},
            "Raw": {"rich_text": {}},
        },
    }
    try:
        response = requests.post(
            "https://api.notion.com/v1/databases",
            headers=_headers(token),
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise NotionDatabaseError(f"Error de red: {exc}") from None

    if response.status_code >= 400:
        _raise_http_error(response.status_code, response.text)
    database_id = response.json().get("id", "")
    if not database_id:
        raise NotionDatabaseError("Notion no devolvió database_id al crear la base.")
    return database_id


def validate_database_schema(token: str, database_id: str) -> None:
    """Validate expected schema for the already-created Notion database."""
    try:
        response = requests.get(
            f"https://api.notion.com/v1/databases/{database_id}",
            headers=_headers(token),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise NotionDatabaseError(f"Error de red: {exc}") from None

    if response.status_code >= 400:
        _raise_http_error(response.status_code, response.text)

    properties = response.json().get("properties", {})
    for name, expected_type in EXPECTED_SCHEMA.items():
        found = properties.get(name)
        if not found:
            raise NotionDatabaseError(f"La base creada no tiene la propiedad requerida: '{name}'.")
        if found.get("type") != expected_type:
            raise NotionDatabaseError(
                f"La propiedad '{name}' tiene tipo '{found.get('type')}', esperado '{expected_type}'."
            )
