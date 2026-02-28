"""AI processing for incoming notes."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres un asistente que analiza notas empresariales.\n"
    "Devuelve SOLO JSON válido con:\n"
    "- resumen (máx 4 líneas)\n"
    "- acciones (lista clara si existen)\n"
    "- tipo_sugerido (Nota, Decisión, Incidencia)\n"
    "- prioridad_sugerida (Baja, Media, Alta)\n"
    "No añadas texto fuera del JSON."
)


@dataclass(slots=True)
class ProcessedNote:
    resumen: str
    acciones: str
    tipo_sugerido: str
    prioridad_sugerida: str


def _empty_processed_note() -> ProcessedNote:
    return ProcessedNote("", "", "", "")


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

    return ProcessedNote(
        resumen=str(payload.get("resumen", "") or "").strip(),
        acciones=str(payload.get("acciones", "") or "").strip(),
        tipo_sugerido=str(payload.get("tipo_sugerido", "") or "").strip(),
        prioridad_sugerida=str(payload.get("prioridad_sugerida", "") or "").strip(),
    )
