"""On-demand, profile-based AI summaries for Knowledge notes."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.core.openai_client import MODEL_NAME, build_openai_client
from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

DEFAULT_SUMMARY_TYPE = "executive_extended"
CHUNK_CHARS = 14_000
MAX_SOURCE_CHARS = 240_000


@dataclass(frozen=True)
class SummaryProfile:
    key: str
    label: str
    instructions: str


SUMMARY_PROFILES = {
    "executive_extended": SummaryProfile("executive_extended", "Ejecutivo amplio", """Genera un resumen ejecutivo amplio y fiel. Debe permitir comprender una reunión, conversación o documento largo sin releerlo completo. Prefiere un resumen largo antes que omitir información importante. Distingue hechos, opiniones, propuestas, decisiones y estimaciones, y conserva discrepancias. Elimina saludos, repeticiones y conversación secundaria.
Estructura sugerida (incluye únicamente apartados con contenido real): RESUMEN EJECUTIVO; Contexto y objetivo; Situación analizada; Principales asuntos tratados; Datos y cifras relevantes; Problemas detectados; Decisiones y acuerdos; Acciones y próximos pasos; Asuntos pendientes; Conclusiones. No inventes acuerdos ni acciones."""),
    "executive_short": SummaryProfile("executive_short", "Ejecutivo breve", "Genera un resumen ejecutivo de lectura rápida, de aproximadamente 5 a 10 párrafos cortos. Prioriza conclusiones y hechos relevantes y conserva las cifras importantes."),
    "meeting_minutes": SummaryProfile("meeting_minutes", "Acta de reunión", """Genera un ACTA DE REUNIÓN con: Fecha; Asistentes; Objetivo; Temas tratados; Acuerdos; Responsables; Plazos; Pendientes; Próxima revisión. Si un dato no aparece escribe «No consta». No infieras responsables ni fechas."""),
    "key_points": SummaryProfile("key_points", "Puntos clave", "Genera entre 5 y 15 puntos clave, con frases cortas y conservando números y fechas importantes."),
    "custom": SummaryProfile("custom", "Personalizado", "Sigue las instrucciones específicas del usuario."),
}


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


def _tags_text(tags: object) -> str:
    if isinstance(tags, str):
        return tags.strip()
    if isinstance(tags, (list, tuple, set)):
        return ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    return ""


def build_summary_source(note: dict[str, Any] | Any, attachments_text: str | None = None) -> str:
    """Build a stable textual source, excluding the existing summary and binary data."""
    summary = str(_value(note, "summary", "") or "").strip()
    indexed = str(_value(note, "indexed_text", "") or "").strip()
    # indexed_text historically contains the summary. Removing the exact value keeps
    # stale detection stable after reindexing while retaining extracted attachment/OCR text.
    if summary and indexed:
        indexed = indexed.replace(summary, "")
    fields = (
        ("Título", _value(note, "title")),
        ("Área", _value(note, "area") or _value(note, "area_name")),
        ("Tema", _value(note, "topic") or _value(note, "topic_name")),
        ("Tipo", _value(note, "tipo") or _value(note, "item_type_name") or _value(note, "type")),
        ("Etiquetas", _tags_text(_value(note, "tags", ""))),
        ("Contenido", _value(note, "content")),
        ("Texto indexado y OCR", indexed),
        ("Texto adicional de adjuntos", attachments_text or ""),
    )
    parts: list[str] = []
    seen: set[str] = set()
    for label, value in fields:
        text = str(value or "").strip()
        normalized = re.sub(r"\s+", " ", text).casefold()
        if text and normalized not in seen:
            parts.append(f"{label}: {text}")
            seen.add(normalized)
    source = "\n\n".join(parts)
    if len(source) > MAX_SOURCE_CHARS:
        logger.warning(
            "KNOWLEDGE_SUMMARY: source context limited chars=%s limit=%s",
            len(source), MAX_SOURCE_CHARS,
        )
        source = source[:MAX_SOURCE_CHARS]
    return source


def calculate_summary_source_hash(note: dict[str, Any] | Any, attachments_text: str | None = None) -> str:
    source = build_summary_source(note, attachments_text)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _base_rules() -> str:
    return """Reglas obligatorias:
- Usa solo la información proporcionada y no inventes ni completes datos ausentes.
- Conserva nombres, fechas, cantidades, porcentajes, importes, €/kg, kilos, hectáreas y toda cifra relevante.
- Redacta en español profesional y claro, salvo que una instrucción personalizada pida otro idioma.
- Devuelve texto limpio. No uses Markdown: nada de ###, **, __ ni cercas de código."""


def _profile_instructions(summary_type: str, custom_prompt: str) -> str:
    profile = SUMMARY_PROFILES.get(summary_type)
    if profile is None:
        raise ValueError(f"Perfil de resumen no válido: {summary_type}")
    if summary_type == "custom":
        custom_prompt = custom_prompt.strip()
        if not custom_prompt:
            raise ValueError("Escribe instrucciones para el resumen personalizado.")
        return f"{profile.instructions}\n\nInstrucciones del usuario:\n{custom_prompt}"
    return profile.instructions


def _clean_output(text: str) -> str:
    text = re.sub(r"^\s*```(?:text|markdown)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def _is_config_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("clave de openai", "api key", "openai no está instalada", "openai' no está instalada", "no se encontró", "inválida"))


def _request(client: Any, prompt: str) -> str:
    response = client.responses.create(model=MODEL_NAME, input=prompt)
    result = _clean_output(OpenAIService._extract_text(response))
    if not result:
        raise KnowledgeSummaryGenerationError("La IA no devolvió ningún resumen.")
    return result


def generate_knowledge_summary(
    note: dict[str, Any] | Any,
    attachments_text: str | None = None,
    summary_type: str = DEFAULT_SUMMARY_TYPE,
    custom_prompt: str = "",
) -> str:
    """Generate a selected summary profile on demand, using map-reduce for long sources."""
    note_id = _value(note, "id", _value(note, "note_id", ""))
    logger.info("KNOWLEDGE_SUMMARY: requested note_id=%s type=%s", note_id, summary_type)
    instructions = _profile_instructions(summary_type, custom_prompt)
    source = build_summary_source(note, attachments_text)
    logger.info("KNOWLEDGE_SUMMARY: source_chars=%s", len(source))
    if not source.strip():
        raise KnowledgeSummaryGenerationError("No hay contenido textual para resumir.")
    chunks = [source[pos : pos + CHUNK_CHARS] for pos in range(0, len(source), CHUNK_CHARS)]
    logger.info("KNOWLEDGE_SUMMARY: chunking=%s chunks=%s", len(chunks) > 1, len(chunks))
    try:
        client = build_openai_client()
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_SUMMARY: error reason=no_ai_config")
        raise KnowledgeSummaryConfigError("No hay configuración IA disponible para generar resumen.") from exc
    try:
        if len(chunks) == 1:
            summary = _request(client, f"{_base_rules()}\n\n{instructions}\n\nFUENTE:\n{source}")
        else:
            partials = []
            for number, chunk in enumerate(chunks, 1):
                partials.append(_request(client, f"""Resume fielmente el bloque {number} de {len(chunks)} para preparar una síntesis posterior. Conserva todos los nombres, cifras, decisiones, discrepancias y pendientes. No inventes. Usa texto limpio sin Markdown.\n\nBLOQUE:\n{chunk}"""))
            mapped = "\n\n".join(f"BLOQUE {i}:\n{text}" for i, text in enumerate(partials, 1))
            summary = _request(client, f"{_base_rules()}\n\n{instructions}\n\nSintetiza estos resúmenes parciales sin perder cifras ni contradicciones y sin mencionar el proceso por bloques:\n{mapped}")
    except KnowledgeSummaryGenerationError:
        logger.error("KNOWLEDGE_SUMMARY: error reason=empty_response")
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_config_error(exc):
            logger.info("KNOWLEDGE_SUMMARY: error reason=no_ai_config")
            raise KnowledgeSummaryConfigError("No hay configuración IA disponible para generar resumen.") from exc
        logger.exception("KNOWLEDGE_SUMMARY: error reason=%s", exc)
        raise KnowledgeSummaryGenerationError("No se pudo generar el resumen IA.") from exc
    logger.info("KNOWLEDGE_SUMMARY: generated chars=%s", len(summary))
    return summary
