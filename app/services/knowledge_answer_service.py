"""AI answers for Knowledge questions grounded in local search results."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.openai_client import MODEL_NAME, build_openai_client
from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

MAX_NOTES = 8
MAX_FIELD_CHARS = 3_500
NO_INFO_ANSWER = "No he encontrado información suficiente en Knowledge para responder con seguridad."


class KnowledgeAnswerConfigError(RuntimeError):
    """Raised when no usable AI configuration is available."""


class KnowledgeAnswerGenerationError(RuntimeError):
    """Raised when the AI service cannot generate an answer."""


def _value(item: dict[str, Any] | Any, key: str, default: Any = "") -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(key, default)
    try:
        return item[key]
    except Exception:  # noqa: BLE001
        return default


def _clean_text(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _trim_text(text: object, limit: int) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n[Texto recortado por límite de contexto]"


def _tags_text(tags: object) -> str:
    if isinstance(tags, str):
        return tags.strip()
    if isinstance(tags, (list, tuple, set)):
        return ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    return ""


def _source_from_result(result: dict[str, Any] | Any) -> dict[str, Any]:
    return {
        "note_id": _value(result, "note_id", _value(result, "id", "")),
        "title": str(_value(result, "title") or "Sin título"),
        "area": str(_value(result, "area") or _value(result, "area_name") or ""),
        "topic": str(_value(result, "topic") or _value(result, "topic_name") or ""),
        "snippet": str(_value(result, "snippet") or ""),
    }


def _score(result: dict[str, Any] | Any) -> float:
    try:
        return float(_value(result, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_label(source: dict[str, Any]) -> str:
    parts = [str(source.get("area") or "").strip(), str(source.get("topic") or "").strip()]
    parts.append(str(source.get("title") or "Sin título").strip())
    return " > ".join(part for part in parts if part)


def _build_note_context(result: dict[str, Any] | Any, index: int) -> str:
    title = str(_value(result, "title") or "Sin título").strip()
    area = str(_value(result, "area") or _value(result, "area_name") or "").strip()
    topic = str(_value(result, "topic") or _value(result, "topic_name") or "").strip()
    item_type = str(_value(result, "type") or _value(result, "tipo") or _value(result, "item_type_name") or "").strip()
    tags = _tags_text(_value(result, "tags", ""))
    snippet = _trim_text(_value(result, "snippet", ""), 900)
    content = _trim_text(_value(result, "content", ""), MAX_FIELD_CHARS)
    indexed_text = _trim_text(_value(result, "indexed_text", ""), MAX_FIELD_CHARS)
    summary = _trim_text(_value(result, "summary", ""), 1_500)

    return f"""
[Nota {index}]
ID: {_value(result, "note_id", _value(result, "id", ""))}
Título: {title}
Área: {area or "No indicada"}
Tema: {topic or "No indicado"}
Tipo: {item_type or "No indicado"}
Etiquetas: {tags or "No indicadas"}
Score local: {_score(result):.2f}
Snippet relevante: {snippet or "[Sin snippet]"}
Resumen local existente: {summary or "[Sin resumen]"}
Contenido relevante/local: {content or "[Sin contenido]"}
Texto indexado local: {indexed_text or "[Sin texto indexado adicional]"}
""".strip()


def _select_context_results(results: list[dict[str, Any]], max_context_chars: int) -> tuple[list[dict[str, Any]], str]:
    selected: list[dict[str, Any]] = []
    chunks: list[str] = []
    budget = max(1_000, int(max_context_chars or 12_000))
    sorted_results = sorted(results, key=lambda item: _score(item), reverse=True)[:MAX_NOTES]

    for result in sorted_results:
        chunk = _build_note_context(result, len(selected) + 1)
        separator = "\n\n---\n\n" if chunks else ""
        projected_length = len("".join(chunks)) + len(separator) + len(chunk)
        if projected_length > budget:
            remaining = budget - len("".join(chunks)) - len(separator)
            if remaining < 500:
                break
            chunk = chunk[:remaining].rstrip() + "\n[Nota recortada por límite total de contexto]"
        selected.append(result)
        chunks.append(separator + chunk)
        if len("".join(chunks)) >= budget:
            break

    return selected, "".join(chunks).strip()


def _build_prompt(question: str, context: str, sources: list[dict[str, Any]]) -> str:
    sources_text = "\n".join(f"- {_source_label(source)}" for source in sources) or "- [Sin fuentes]"
    return f"""
Eres un asistente de Sansebas Nexus para responder preguntas sobre Knowledge local.

Reglas estrictas:
- Responde siempre en español.
- Usa solo el contexto proporcionado abajo. No uses internet ni conocimiento externo.
- No inventes datos, fechas, personas, empresas, recetas ni conclusiones.
- Si el contexto no contiene información suficiente, responde exactamente: "{NO_INFO_ANSWER}"
- Diferencia claramente los hechos encontrados de deducciones razonables. Si haces una deducción, márcala como "Deducción:".
- Cita las notas fuente al final usando sus títulos/ruta.
- No menciones notas que no estén en las fuentes consultadas.

Formato obligatorio:
Respuesta:
...

Hechos encontrados:
- ...

Deducciones:
- ...

Fuentes consultadas:
{sources_text}

Pregunta del usuario:
{question.strip()}

Contexto local de Knowledge:
{context or "[Sin contexto local]"}
""".strip()


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


def answer_question_from_knowledge(
    question: str,
    results: list[dict[str, Any]],
    max_context_chars: int = 12_000,
) -> dict[str, Any]:
    """Answer a question using only already-found local Knowledge results as context."""
    cleaned_question = str(question or "").strip()
    logger.info('KNOWLEDGE_ANSWER: requested question="%s"', cleaned_question)

    if not cleaned_question or not results:
        logger.info("KNOWLEDGE_ANSWER: context_results=0 chars=0")
        logger.info("KNOWLEDGE_ANSWER: generated chars=%s", len(NO_INFO_ANSWER))
        return {"answer": NO_INFO_ANSWER, "sources": []}

    selected_results, context = _select_context_results(results, max_context_chars=max_context_chars)
    sources = [_source_from_result(result) for result in selected_results]
    logger.info("KNOWLEDGE_ANSWER: context_results=%s chars=%s", len(selected_results), len(context))

    if not context:
        logger.info("KNOWLEDGE_ANSWER: generated chars=%s", len(NO_INFO_ANSWER))
        return {"answer": NO_INFO_ANSWER, "sources": []}

    prompt = _build_prompt(cleaned_question, context, sources)
    try:
        client = build_openai_client()
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_ANSWER: no_ai_config")
        raise KnowledgeAnswerConfigError("No hay configuración IA disponible.") from exc

    try:
        response = client.responses.create(model=MODEL_NAME, input=prompt)
        answer = OpenAIService._extract_text(response).strip()
    except Exception as exc:  # noqa: BLE001
        if _is_config_error(exc):
            logger.info("KNOWLEDGE_ANSWER: no_ai_config")
            raise KnowledgeAnswerConfigError("No hay configuración IA disponible.") from exc
        logger.exception("KNOWLEDGE_ANSWER: error reason=%s", exc)
        raise KnowledgeAnswerGenerationError("No se pudo generar la respuesta IA.") from exc

    if not answer:
        logger.error("KNOWLEDGE_ANSWER: error reason=empty_response")
        raise KnowledgeAnswerGenerationError("La IA no devolvió ninguna respuesta.")

    logger.info("KNOWLEDGE_ANSWER: generated chars=%s", len(answer))
    return {"answer": answer, "sources": sources}
