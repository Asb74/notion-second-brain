"""Service wrapper for robust OpenAI responses parsing."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.openai_client import build_openai_client

logger = logging.getLogger(__name__)


class OpenAIService:
    @staticmethod
    def generate_text(prompt: str, model: str = "gpt-4o-mini") -> str:
        try:
            client = build_openai_client()
            response = client.responses.create(model=model, input=prompt)
            return OpenAIService._extract_text(response)
        except Exception:  # noqa: BLE001
            logger.exception("Error generando texto OpenAI")
            return ""

    @staticmethod
    def generate_json(prompt: str, model: str = "gpt-4o-mini") -> Optional[dict]:
        """Genera JSON seguro desde la IA."""
        try:
            client = build_openai_client()
            response = client.responses.create(
                model=model,
                input=prompt,
                text={"format": {"type": "json_object"}},
            )
            raw_text = OpenAIService._extract_text(response)
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError as exc:
                logger.warning("JSON inválido recibido: %s", exc)
                logger.warning("Contenido: %s", raw_text)
                cleaned = OpenAIService._safe_json_extract(raw_text)
                if cleaned:
                    return cleaned
                return None
        except Exception:  # noqa: BLE001
            logger.exception("Error generando JSON OpenAI")
            return None

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Extrae texto de forma robusta desde response.output."""
        try:
            if hasattr(response, "output_text") and response.output_text:
                return str(response.output_text).strip()

            texts: list[str] = []
            for item in getattr(response, "output", []):
                for content in getattr(item, "content", []):
                    if getattr(content, "text", None):
                        texts.append(str(content.text))

            return "\n".join(texts).strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _safe_json_extract(text: str) -> Optional[dict]:
        """Intenta recuperar JSON aunque venga sucio."""
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
        except Exception:  # noqa: BLE001
            return None

        return None
