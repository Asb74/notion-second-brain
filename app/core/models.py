"""Domain models for the Notion second brain desktop app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class NoteStatus(str, Enum):
    """Synchronization status for a local note."""

    PENDING = "pendiente"
    SENT = "enviado"
    ERROR = "error"


@dataclass(slots=True)
class NoteCreateRequest:
    """Input payload used when creating a local note."""

    raw_text: str
    source: str
    area: str
    tipo: str
    estado: str
    prioridad: str
    fecha: str
    title: str = ""
    resumen: str = ""
    acciones: str = ""


@dataclass(slots=True)
class Note:
    """Persisted note entity."""

    id: int
    created_at: str
    source: str
    source_id: str
    title: str
    raw_text: str
    area: str
    tipo: str
    estado: str
    prioridad: str
    fecha: str
    resumen: str
    acciones: str
    status: str
    notion_page_id: Optional[str]
    last_error: Optional[str]
    attempts: int
    next_retry_at: Optional[str]


@dataclass(slots=True)
class AppSettings:
    """User-configurable application settings."""

    notion_token: str = ""
    notion_database_id: str = ""
    default_area: str = ""
    default_tipo: str = ""
    default_estado: str = "Pendiente"
    default_prioridad: str = "Media"
    prop_title: str = "Actividad"
    prop_area: str = "Area"
    prop_tipo: str = "Tipo"
    prop_estado: str = "Estado"
    prop_fecha: str = "Fecha"
    prop_prioridad: str = "Prioridad"
    max_attempts: int = 5
    retry_delay_seconds: int = 60

    @staticmethod
    def now_iso() -> str:
        """Return current UTC datetime in ISO format."""
        return datetime.utcnow().isoformat(timespec="seconds")
