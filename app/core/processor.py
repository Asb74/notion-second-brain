"""AI processing for incoming notes."""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass

from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "OBJETIVO: Analizar el texto y determinar si las acciones deben ser simples o desglosadas.\n"
    "REGLAS:\n"
    "1. Si el texto contiene una única obligación clara, modo = simple.\n"
    "2. Si contiene múltiples objetivos independientes, modo = desglosado.\n"
    "3. Si no hay acciones, modo = ninguna.\n"
    "4. Si no se puede determinar claramente, modo = ambiguo.\n"
    "5. En modo simple, devolver una acción consolidada clara y operativa.\n"
    "6. En modo desglosado, devolver acción principal con subtareas separadas.\n"
    "7. No inventar complejidad si no existe.\n"
    "FORMATO JSON:\n"
    "{\n"
    '  "modo": "simple" | "desglosado" | "ninguna" | "ambiguo",\n'
    '  "acciones": [\n'
    "    {\n"
    '      "descripcion": "...",\n'
    '      "subtareas": ["..."]\n'
    "    }\n"
    "  ]\n"
    "}\n"
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


def _normalize_actions(raw_actions: object) -> list[str]:
    if isinstance(raw_actions, str):
        try:
            raw_actions = ast.literal_eval(raw_actions)
        except Exception:  # noqa: BLE001
            raw_actions = [raw_actions]

    if raw_actions is None:
        return []

    if not isinstance(raw_actions, list):
        raw_actions = [str(raw_actions)]

    actions: list[str] = []
    for value in raw_actions:
        if isinstance(value, dict):
            description = str(value.get("descripcion", "") or "").strip()
            if description:
                actions.append(description)
            subtasks = value.get("subtareas", [])
            if isinstance(subtasks, list):
                for subtask in subtasks:
                    subtask_text = str(subtask or "").strip()
                    if subtask_text:
                        actions.append(subtask_text)
            continue
        if not isinstance(value, str):
            continue
        chunks = [chunk.strip(" -•\t") for chunk in value.replace("\r", "\n").split("\n")]
        actions.extend(chunk for chunk in chunks if chunk)

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
