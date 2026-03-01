"""AI processing for incoming notes."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """[PEGA AQUÍ EL PROMPT NUEVO COMPLETO]"""


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


def _normalize_actions(raw_actions: object) -> list[str]:
    if raw_actions is None:
        return []

    if not isinstance(raw_actions, list):
        raw_actions = [raw_actions]

    actions: list[str] = []
    for value in raw_actions:
        if isinstance(value, dict):
            description = str(value.get("descripcion", "") or "").strip()
            raw_context = value.get("contexto", [])
            raw_date = value.get("fecha_detectada", None)

            contexts: list[str] = []
            if isinstance(raw_context, list):
                contexts = [str(item).strip() for item in raw_context if str(item).strip()]

            date_text = str(raw_date).strip() if raw_date is not None else ""
            if date_text.lower() == "null":
                date_text = ""

            if description:
                details: list[str] = []
                if contexts:
                    details.append(f"Contexto: {', '.join(contexts)}")
                if date_text:
                    details.append(f"Fecha: {date_text}")

                if details:
                    actions.append(f"{description} [{'; '.join(details)}]")
                else:
                    actions.append(description)
            continue

        value_text = str(value or "").strip()
        if value_text:
            actions.append(value_text)

    return actions


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

    acciones = _normalize_actions(payload.get("acciones", []))

    return ProcessedNote(
        resumen=str(payload.get("resumen", "") or "").strip(),
        acciones=acciones,
        tipo_sugerido=str(payload.get("tipo_sugerido", "") or "").strip(),
        prioridad_sugerida=str(payload.get("prioridad_sugerida", "") or "").strip(),
    )
