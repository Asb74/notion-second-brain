"""AI answers for Knowledge questions grounded in local search results."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.openai_client import MODEL_NAME, build_openai_client
from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

MAX_NOTES = 8
MAX_FEDERATED_RESULTS = 8
MAX_FIELD_CHARS = 3_500
NO_INFO_ANSWER = (
    "No he encontrado información suficiente en Knowledge para responder con seguridad."
)
FEDERATED_NO_INFO_ANSWER = "No he encontrado información suficiente en los resultados federados para responder con seguridad."


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


def _raw_result(result: dict[str, Any] | Any) -> dict[str, Any] | Any:
    raw = _value(result, "raw", None)
    return raw if isinstance(raw, dict) else result


def _result_value(result: dict[str, Any] | Any, key: str, default: Any = "") -> Any:
    value = _value(result, key, None)
    if value not in (None, ""):
        return value
    raw = _raw_result(result)
    if raw is result:
        return default
    return _value(raw, key, default)


def _source_from_result(result: dict[str, Any] | Any) -> dict[str, Any]:
    return {
        "note_id": _result_value(result, "note_id", _result_value(result, "id", "")),
        "title": str(_result_value(result, "title") or "Sin título"),
        "area": str(
            _result_value(result, "area", _result_value(result, "area_name", "")) or ""
        ),
        "topic": str(
            _result_value(result, "topic", _result_value(result, "topic_name", ""))
            or ""
        ),
        "snippet": str(_result_value(result, "snippet") or ""),
    }


def _knowledge_navigable_source_from_result(
    result: dict[str, Any] | Any,
) -> dict[str, Any]:
    note_id = _result_value(result, "note_id", _result_value(result, "id", ""))
    return {
        "source": "knowledge",
        "id": note_id,
        "note_id": note_id,
        "title": str(_result_value(result, "title") or "Sin título"),
        "area": str(
            _result_value(
                result,
                "area",
                _result_value(
                    result, "area_name", _result_value(result, "subtitle", "")
                ),
            )
            or ""
        ),
        "topic": str(
            _result_value(result, "topic", _result_value(result, "topic_name", ""))
            or ""
        ),
        "type": str(
            _result_value(
                result,
                "type",
                _result_value(
                    result, "tipo", _result_value(result, "item_type_name", "")
                ),
            )
            or ""
        ),
        "date": str(
            _result_value(result, "updated_at", _result_value(result, "date", "")) or ""
        ),
        "match_source": str(_result_value(result, "match_source", "") or ""),
        "snippet": str(_result_value(result, "snippet") or ""),
    }


def _email_source_from_result(result: dict[str, Any] | Any) -> dict[str, Any]:
    raw = _raw_result(result)
    gmail_id = str(
        _result_value(result, "gmail_id", _result_value(result, "id", "")) or ""
    )
    email_id = str(_result_value(result, "email_id", gmail_id) or "")
    message_id = str(_result_value(result, "message_id", "") or "")
    thread_id = str(_result_value(result, "thread_id", "") or "")
    sender = str(
        _result_value(
            result,
            "real_sender",
            _result_value(
                result,
                "sender",
                _result_value(
                    result, "original_from", _result_value(result, "subtitle", "")
                ),
            ),
        )
        or ""
    )
    source = {
        "source": "email",
        "id": str(_result_value(result, "id", gmail_id or email_id) or ""),
        "email_id": email_id,
        "gmail_id": gmail_id,
        "message_id": message_id,
        "thread_id": thread_id,
        "subject": str(
            _result_value(
                result, "subject", _result_value(result, "title", "Sin asunto")
            )
            or "Sin asunto"
        ),
        "from": sender,
        "sender": sender,
        "date": str(
            _result_value(result, "received_at", _result_value(result, "date", ""))
            or ""
        ),
        "match_source": str(_result_value(result, "match_source", "") or ""),
        "snippet": str(_result_value(result, "snippet") or ""),
        "body": str(
            _result_value(result, "body_text", _result_value(result, "body", "")) or ""
        ),
        "raw": dict(raw) if isinstance(raw, dict) else {},
    }
    logger.info(
        "FEDERATED_ANSWER: source email ids id=%s email_id=%s gmail_id=%s",
        source["id"],
        source["email_id"],
        source["gmail_id"],
    )
    return source


def _score(result: dict[str, Any] | Any) -> float:
    try:
        return float(_value(result, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_label(source: dict[str, Any]) -> str:
    parts = [
        str(source.get("area") or "").strip(),
        str(source.get("topic") or "").strip(),
    ]
    parts.append(str(source.get("title") or "Sin título").strip())
    return " > ".join(part for part in parts if part)


def _email_source_label(source: dict[str, Any]) -> str:
    details = [f"Asunto: {source.get('subject') or 'Sin asunto'}"]
    if source.get("sender"):
        details.append(f"Remitente: {source['from']}")
    if source.get("date"):
        details.append(f"Fecha: {source['date']}")
    return " | ".join(details)


def _build_note_context(result: dict[str, Any] | Any, index: int) -> str:
    title = str(_result_value(result, "title") or "Sin título").strip()
    area = str(
        _result_value(result, "area", _result_value(result, "area_name", "")) or ""
    ).strip()
    topic = str(
        _result_value(result, "topic", _result_value(result, "topic_name", "")) or ""
    ).strip()
    item_type = str(
        _result_value(
            result,
            "type",
            _result_value(result, "tipo", _result_value(result, "item_type_name", "")),
        )
        or ""
    ).strip()
    tags = _tags_text(_result_value(result, "tags", ""))
    snippet = _trim_text(_result_value(result, "snippet", ""), 900)
    content = _trim_text(_result_value(result, "content", ""), MAX_FIELD_CHARS)
    indexed_text = _trim_text(
        _result_value(result, "indexed_text", ""), MAX_FIELD_CHARS
    )
    summary = _trim_text(_result_value(result, "summary", ""), 1_500)

    return f"""
[Nota {index}]
ID: {_result_value(result, "note_id", _result_value(result, "id", ""))}
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


def _select_context_results(
    results: list[dict[str, Any]], max_context_chars: int
) -> tuple[list[dict[str, Any]], str]:
    selected: list[dict[str, Any]] = []
    chunks: list[str] = []
    budget = max(1_000, int(max_context_chars or 12_000))
    sorted_results = sorted(results, key=lambda item: _score(item), reverse=True)[
        :MAX_NOTES
    ]

    for result in sorted_results:
        chunk = _build_note_context(result, len(selected) + 1)
        separator = "\n\n---\n\n" if chunks else ""
        projected_length = len("".join(chunks)) + len(separator) + len(chunk)
        if projected_length > budget:
            remaining = budget - len("".join(chunks)) - len(separator)
            if remaining < 500:
                break
            chunk = (
                chunk[:remaining].rstrip()
                + "\n[Nota recortada por límite total de contexto]"
            )
        selected.append(result)
        chunks.append(separator + chunk)
        if len("".join(chunks)) >= budget:
            break

    return selected, "".join(chunks).strip()


def _attachment_names_from_value(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return text
    if not isinstance(value, (list, tuple)):
        return ""
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("filename") or item.get("name") or "").strip()
            if name:
                names.append(name)
        elif str(item).strip():
            names.append(str(item).strip())
    return ", ".join(names)


def _build_email_context(result: dict[str, Any] | Any, index: int) -> str:
    subject = str(
        _result_value(result, "subject", _result_value(result, "title", "Sin asunto"))
        or "Sin asunto"
    ).strip()
    sender = str(
        _result_value(
            result,
            "real_sender",
            _result_value(
                result,
                "sender",
                _result_value(
                    result, "original_from", _result_value(result, "subtitle", "")
                ),
            ),
        )
        or ""
    ).strip()
    recipients = str(
        _result_value(result, "original_to", _result_value(result, "recipients", ""))
        or ""
    ).strip()
    cc = str(_result_value(result, "original_cc", "") or "").strip()
    date = str(
        _result_value(result, "received_at", _result_value(result, "date", "")) or ""
    ).strip()
    snippet = _trim_text(_result_value(result, "snippet", ""), 900)
    body = _trim_text(
        _result_value(result, "body_text", _result_value(result, "body", "")),
        MAX_FIELD_CHARS,
    )
    attachments = _attachment_names_from_value(
        _result_value(
            result, "attachments_json", _result_value(result, "attachments", "")
        )
    )

    return f"""
[Email {index}]
ID local/Gmail: {_result_value(result, "gmail_id", _result_value(result, "id", ""))}
Asunto: {subject}
Remitente: {sender or "No indicado"}
Destinatarios: {recipients or "No indicados"}
CC: {cc or "No indicado"}
Fecha: {date or "No indicada"}
Adjuntos: {attachments or "Sin adjuntos indicados"}
Score local: {_score(result):.2f}
Snippet relevante: {snippet or "[Sin snippet]"}
Cuerpo local disponible: {body or snippet or "[Sin cuerpo local; usando solo snippet si existe]"}
""".strip()


def _is_email_result(result: dict[str, Any] | Any) -> bool:
    return str(_value(result, "source", "") or "").lower() == "email"


def _select_federated_context_results(
    results: list[dict[str, Any]], max_context_chars: int
) -> tuple[list[dict[str, Any]], str]:
    selected: list[dict[str, Any]] = []
    chunks: list[str] = []
    budget = max(1_000, int(max_context_chars or 12_000))
    sorted_results = sorted(results, key=lambda item: _score(item), reverse=True)[
        :MAX_FEDERATED_RESULTS
    ]
    knowledge_index = 0
    email_index = 0

    for result in sorted_results:
        if _is_email_result(result):
            email_index += 1
            chunk = _build_email_context(result, email_index)
        else:
            knowledge_index += 1
            chunk = _build_note_context(result, knowledge_index)
        separator = "\n\n---\n\n" if chunks else ""
        projected_length = len("".join(chunks)) + len(separator) + len(chunk)
        if projected_length > budget:
            remaining = budget - len("".join(chunks)) - len(separator)
            if remaining < 500:
                break
            chunk = (
                chunk[:remaining].rstrip()
                + "\n[Resultado recortado por límite total de contexto]"
            )
        selected.append(result)
        chunks.append(separator + chunk)
        if len("".join(chunks)) >= budget:
            break

    return selected, "".join(chunks).strip()


def _build_federated_prompt(
    question: str,
    context: str,
    knowledge_sources: list[dict[str, Any]],
    email_sources: list[dict[str, Any]],
) -> str:
    knowledge_text = (
        "\n".join(f"- {_source_label(source)}" for source in knowledge_sources)
        or "- [Sin fuentes Knowledge]"
    )
    email_text = (
        "\n".join(f"- {_email_source_label(source)}" for source in email_sources)
        or "- [Sin fuentes Email]"
    )
    return f"""
Eres un asistente de Sansebas Nexus para responder preguntas usando resultados locales federados de Knowledge y Emails.

Reglas estrictas:
- Responde siempre en español.
- Usa solo el contexto proporcionado abajo. No uses internet ni conocimiento externo.
- No inventes datos, fechas, personas, empresas, recetas ni conclusiones.
- Indica si cada información viene de Knowledge o de Email.
- No mezcles emails como si fueran notas Knowledge.
- Si el contexto no contiene información suficiente, responde claramente que no hay datos suficientes.
- Cita fuentes únicamente por el título/asunto exacto incluido en las listas de fuentes consultadas.
- No menciones fuentes que no estén en las fuentes consultadas.
- Si la fecha no está clara o no está indicada en el contexto, dilo explícitamente.

Formato obligatorio:
Respuesta:
...

Información encontrada:
- [Knowledge/Email] ...

Fuentes:
Knowledge:
{knowledge_text}

Emails:
{email_text}

Pregunta del usuario:
{question.strip()}

Contexto local federado:
{context or "[Sin contexto local]"}
""".strip()


def _build_prompt(question: str, context: str, sources: list[dict[str, Any]]) -> str:
    sources_text = (
        "\n".join(f"- {_source_label(source)}" for source in sources)
        or "- [Sin fuentes]"
    )
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

    selected_results, context = _select_context_results(
        results, max_context_chars=max_context_chars
    )
    sources = [_source_from_result(result) for result in selected_results]
    logger.info(
        "KNOWLEDGE_ANSWER: context_results=%s chars=%s",
        len(selected_results),
        len(context),
    )

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
            raise KnowledgeAnswerConfigError(
                "No hay configuración IA disponible."
            ) from exc
        logger.exception("KNOWLEDGE_ANSWER: error reason=%s", exc)
        raise KnowledgeAnswerGenerationError(
            "No se pudo generar la respuesta IA."
        ) from exc

    if not answer:
        logger.error("KNOWLEDGE_ANSWER: error reason=empty_response")
        raise KnowledgeAnswerGenerationError("La IA no devolvió ninguna respuesta.")

    logger.info("KNOWLEDGE_ANSWER: generated chars=%s", len(answer))
    return {"answer": answer, "sources": sources}


def answer_question_from_federated_results(
    question: str,
    results: list[dict[str, Any]],
    max_context_chars: int = 12_000,
) -> dict[str, Any]:
    """Answer a question using only already-visible federated Knowledge/email results."""
    cleaned_question = str(question or "").strip()
    logger.info("FEDERATED_ANSWER: requested query=%s", cleaned_question)

    if not cleaned_question or not results:
        logger.info("FEDERATED_ANSWER: sources knowledge=0 emails=0")
        logger.info("FEDERATED_ANSWER: context chars=0")
        logger.info(
            "FEDERATED_ANSWER: generated chars=%s", len(FEDERATED_NO_INFO_ANSWER)
        )
        return {
            "answer": FEDERATED_NO_INFO_ANSWER,
            "sources": {"knowledge": [], "emails": []},
            "navigable_sources": [],
        }

    selected_results, context = _select_federated_context_results(
        results, max_context_chars=max_context_chars
    )
    knowledge_sources = [
        _knowledge_navigable_source_from_result(result)
        for result in selected_results
        if not _is_email_result(result)
    ]
    email_sources = [
        _email_source_from_result(result)
        for result in selected_results
        if _is_email_result(result)
    ]
    based_on = (
        "both"
        if knowledge_sources and email_sources
        else "knowledge" if knowledge_sources else "email" if email_sources else "none"
    )
    logger.info("FEDERATED_ANSWER: based_on=%s", based_on)
    logger.info(
        "FEDERATED_ANSWER: sources knowledge=%s emails=%s",
        len(knowledge_sources),
        len(email_sources),
    )
    logger.info("FEDERATED_ANSWER: context chars=%s", len(context))

    if not context:
        logger.info(
            "FEDERATED_ANSWER: generated chars=%s", len(FEDERATED_NO_INFO_ANSWER)
        )
        return {
            "answer": FEDERATED_NO_INFO_ANSWER,
            "sources": {"knowledge": [], "emails": []},
            "navigable_sources": [],
        }

    prompt = _build_federated_prompt(
        cleaned_question, context, knowledge_sources, email_sources
    )
    try:
        client = build_openai_client()
    except Exception as exc:  # noqa: BLE001
        logger.info("FEDERATED_ANSWER: no_ai_config")
        raise KnowledgeAnswerConfigError("No hay configuración IA disponible.") from exc

    try:
        response = client.responses.create(model=MODEL_NAME, input=prompt)
        answer = OpenAIService._extract_text(response).strip()
    except Exception as exc:  # noqa: BLE001
        if _is_config_error(exc):
            logger.info("FEDERATED_ANSWER: no_ai_config")
            raise KnowledgeAnswerConfigError(
                "No hay configuración IA disponible."
            ) from exc
        logger.exception("FEDERATED_ANSWER: error reason=%s", exc)
        raise KnowledgeAnswerGenerationError(
            "No se pudo generar la respuesta IA."
        ) from exc

    if not answer:
        logger.error("FEDERATED_ANSWER: error reason=empty_response")
        raise KnowledgeAnswerGenerationError("La IA no devolvió ninguna respuesta.")

    logger.info("FEDERATED_ANSWER: generated chars=%s", len(answer))
    return {
        "answer": answer,
        "sources": {"knowledge": knowledge_sources, "emails": email_sources},
        "navigable_sources": [*knowledge_sources, *email_sources],
    }
