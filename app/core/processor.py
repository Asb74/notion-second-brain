"""AI processing for incoming notes."""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass

from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres un asistente que analiza notas empresariales.\n"
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

    return ProcessedNote(
        resumen=str(payload.get("resumen", "") or "").strip(),
        acciones=acciones,
        tipo_sugerido=str(payload.get("tipo_sugerido", "") or "").strip(),
        prioridad_sugerida=str(payload.get("prioridad_sugerida", "") or "").strip(),
    )
