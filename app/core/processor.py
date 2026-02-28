"""AI processing for incoming notes."""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass

from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "OBJETIVO: Extraer acciones operativas del texto y clasificarlas por tipo operativo.\n"
    "INSTRUCCIONES:\n"
    "1. Detectar acciones explícitas e implícitas.\n"
    "2. Determinar si las acciones deben ser simples o desglosadas.\n"
    "3. Clasificar cada acción en uno o varios tipos operativos.\n"
    "TIPOS PERMITIDOS:\n"
    "- Llamar\n"
    "- Enviar información\n"
    "- Preparar\n"
    "- Revisar\n"
    "- Confirmar\n"
    "- Reunión\n"
    "- Programar\n"
    "- Seguimiento\n"
    "- Administrativa\n"
    "- Informativa\n"
    "REGLAS DE CLASIFICACIÓN:\n"
    "- Si comienza por 'Llamar' → Llamar\n"
    "- Si implica envío de datos/documentación → Enviar información\n"
    "- Si implica elaboración de documento → Preparar\n"
    "- Si implica verificación → Revisar\n"
    "- Si implica validación externa → Confirmar\n"
    "- Si es coordinación futura → Reunión\n"
    "- Si implica planificación → Programar\n"
    "- Si es control posterior → Seguimiento\n"
    "- Si es gestión interna genérica → Administrativa\n"
    "- Si no requiere acción → Informativa\n"
    "FORMATO JSON OBLIGATORIO:\n"
    "{\n"
    '  "modo": "simple" | "desglosado" | "ninguna" | "ambiguo",\n'
    '  "acciones": [\n'
    "    {\n"
    '      "descripcion": "...",\n'
    '      "subtareas": ["..."],\n'
    '      "tipo_accion": ["..."]\n'
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
            raw_types = value.get("tipo_accion", [])
            action_types: list[str] = []
            if isinstance(raw_types, list):
                action_types = [str(item).strip() for item in raw_types if str(item).strip()]
            if description:
                if action_types:
                    actions.append(f"{description} [Tipo: {', '.join(action_types)}]")
                else:
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
