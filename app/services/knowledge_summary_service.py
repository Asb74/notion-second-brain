"""On-demand AI summaries for Knowledge notes."""

from __future__ import annotations

import logging
from typing import Any

from app.core.openai_client import MODEL_NAME, build_openai_client
from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 12_000
MAX_INDEXED_TEXT_CHARS = 18_000
MAX_ATTACHMENTS_TEXT_CHARS = 10_000
MAX_PROMPT_CHARS = 34_000


class KnowledgeSummaryConfigError(RuntimeError):
    """Raised when no usable AI configuration is available."""


class KnowledgeSummaryGenerationError(RuntimeError):
    """Raised when the AI service cannot generate a summary."""


def _value(note: dict[str, Any] | Any, key: str, default: Any = "") -> Any:
    if note is None:
        return default
    if isinstance(note, dict):
        return note.get(key, default)
    try:
        return note[key]
    except Exception:  # noqa: BLE001
        return default


def _trim_text(text: object, limit: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n[Texto recortado por límite de contexto]"


def _tags_text(tags: object) -> str:
    if isinstance(tags, str):
        return tags.strip()
    if isinstance(tags, (list, tuple, set)):
        return ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    return ""


def _build_prompt(note: dict[str, Any] | Any, attachments_text: str | None = None) -> str:
    title = str(_value(note, "title") or "").strip()
    area = str(_value(note, "area") or _value(note, "area_name") or "").strip()
    topic = str(_value(note, "topic") or _value(note, "topic_name") or "").strip()
    item_type = str(_value(note, "tipo") or _value(note, "item_type_name") or _value(note, "type") or "").strip()
    tags = _tags_text(_value(note, "tags", ""))
    content = _trim_text(_value(note, "content", ""), MAX_CONTENT_CHARS)
    indexed_text = _trim_text(_value(note, "indexed_text", ""), MAX_INDEXED_TEXT_CHARS)
    attachments = _trim_text(attachments_text or "", MAX_ATTACHMENTS_TEXT_CHARS)

    prompt = f"""
Eres un asistente de Knowledge Manager. Genera un resumen en español para una nota local.

Reglas estrictas:
- Resume solo la información incluida abajo.
- No inventes datos, conclusiones, fechas, personas ni empresas.
- Si faltan datos relevantes, indícalo de forma breve.
- Sé claro, breve y estructurado.
- Prioriza el contenido de la nota; usa el texto indexado o de adjuntos solo como apoyo.
- No incluyas información binaria ni referencias a archivos no textuales.

Formato obligatorio:
Resumen:
...

Puntos clave:
- ...
- ...

Fechas/personas/empresas detectadas:
- ...

Datos de la nota:
Título: {title or "Sin título"}
Área: {area or "No indicada"}
Tema: {topic or "No indicado"}
Tipo: {item_type or "No indicado"}
Etiquetas: {tags or "No indicadas"}

Contenido de la nota:
{content or "[Sin contenido]"}

Texto indexado local:
{indexed_text or "[Sin texto indexado adicional]"}

Texto adicional de adjuntos:
{attachments or "[Sin texto adicional de adjuntos]"}
""".strip()
    return _trim_text(prompt, MAX_PROMPT_CHARS)


def _is_config_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "clave de openai",
            "api key",
            "openai no está instalada",
            "openai' no está instalada",
            "no se encontró",
            "inválida",
        )
    )


def generate_knowledge_summary(note: dict[str, Any] | Any, attachments_text: str | None = None) -> str:
    """Generate an on-demand Spanish summary for a Knowledge note using the app AI config."""
    note_id = _value(note, "id", _value(note, "note_id", ""))
    logger.info("KNOWLEDGE_SUMMARY: requested note_id=%s", note_id)
    prompt = _build_prompt(note, attachments_text=attachments_text)
    try:
        client = build_openai_client()
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_SUMMARY: skipped no_ai_config")
        raise KnowledgeSummaryConfigError("No hay configuración IA disponible para generar resumen.") from exc

    try:
        response = client.responses.create(model=MODEL_NAME, input=prompt)
        summary = OpenAIService._extract_text(response).strip()
    except Exception as exc:  # noqa: BLE001
        if _is_config_error(exc):
            logger.info("KNOWLEDGE_SUMMARY: skipped no_ai_config")
            raise KnowledgeSummaryConfigError("No hay configuración IA disponible para generar resumen.") from exc
        logger.exception("KNOWLEDGE_SUMMARY: error reason=%s", exc)
        raise KnowledgeSummaryGenerationError("No se pudo generar el resumen IA.") from exc

    if not summary:
        logger.error("KNOWLEDGE_SUMMARY: error reason=empty_response")
        raise KnowledgeSummaryGenerationError("La IA no devolvió ningún resumen.")

    logger.info("KNOWLEDGE_SUMMARY: generated chars=%s", len(summary))
    return summary
