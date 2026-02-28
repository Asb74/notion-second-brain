"""AI processing for incoming notes."""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass

from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "OBJETIVO: Extraer acciones explícitas e implícitas del texto.\n"
    "Eres un asistente que analiza notas empresariales.\n"
    "Identifica órdenes directas, peticiones formales, compromisos con fecha límite, "
    "obligaciones operativas y acciones que requieran intervención.\n"
    "Si el texto contiene expresiones como 'ha de ser', 'debe', 'para que', 'se solicita', "
    "'es necesario', 'antes del' o una fecha límite, genera al menos una acción.\n"
    "Reformula cada acción en formato operativo claro y accionable.\n"
    "No omitas tareas implícitas.\n"
    "Devuelve SOLO JSON válido con:\n"
    "- resumen (máx 4 líneas)\n"
    "- acciones (array JSON real si existen, nunca string)\n"
    "- tipo_sugerido (Nota, Decisión, Incidencia)\n"
    "- prioridad_sugerida (Baja, Media, Alta)\n"
    "El campo 'acciones' debe ser un array JSON real, no un string.\n"
    "No añadas texto fuera del JSON."
)


@dataclass(slots=True)
class ProcessedNote:
    resumen: str
    acciones: list[str]
    tipo_sugerido: str
    prioridad_sugerida: str


def _empty_processed_note() -> ProcessedNote:
    return ProcessedNote("", [], "", "")


def _extract_json_object(content: str) -> dict:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise
        return json.loads(content[start : end + 1])


ACTION_TRIGGER_PATTERNS = [
    r"\bha de ser\b",
    r"\bdebe\b",
    r"\bpara que\b",
    r"\bse solicita\b",
    r"\bes necesario\b",
    r"\bantes del\b",
    r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
]


def _requires_action(text: str) -> bool:
    lower_text = text.lower()
    return any(re.search(pattern, lower_text) for pattern in ACTION_TRIGGER_PATTERNS)


def _build_fallback_action(text: str) -> str:
    compact_text = " ".join(text.split())
    snippet = compact_text[:160].rstrip(" ,.;:")
    return f"Definir y ejecutar la acción requerida según lo indicado: {snippet}."


def process_text(text: str) -> ProcessedNote:
    """Process note text with OpenAI and return structured suggestion data."""
    if not text.strip():
        return _empty_processed_note()

    try:
        client = build_openai_client()
        response = client.responses.create(
            model=MODEL_NAME,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text[:4000]},
            ],
        )
        payload = _extract_json_object(response.output_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("No se pudo procesar texto con OpenAI: %s", exc)
        return _empty_processed_note()

    acciones = payload.get("acciones", [])
    if isinstance(acciones, str):
        try:
            acciones = ast.literal_eval(acciones)
        except Exception:  # noqa: BLE001
            acciones = [acciones]

    if acciones is None:
        acciones = []

    if not isinstance(acciones, list):
        acciones = [str(acciones)]

    acciones = [a.strip() for a in acciones if isinstance(a, str) and a.strip()]

    if not acciones and _requires_action(text):
        acciones = [_build_fallback_action(text)]

    return ProcessedNote(
        resumen=str(payload.get("resumen", "") or "").strip(),
        acciones=acciones,
        tipo_sugerido=str(payload.get("tipo_sugerido", "") or "").strip(),
        prioridad_sugerida=str(payload.get("prioridad_sugerida", "") or "").strip(),
    )
