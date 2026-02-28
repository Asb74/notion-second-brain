"""OpenAI client utilities."""

from __future__ import annotations

from pathlib import Path

MODEL_NAME = "gpt-4o-mini"
API_KEY_PATH = Path.home() / "AppData" / "Roaming" / "NotionSecondBrain" / "KeySecret.txt"


def load_api_key() -> str:
    """Load API key from plain text file and validate expected format."""
    if not API_KEY_PATH.exists():
        raise RuntimeError(
            f"No se encontró la clave de OpenAI en: {API_KEY_PATH}. "
            "Crea el archivo con la clave en texto plano."
        )

    key = API_KEY_PATH.read_text(encoding="utf-8").strip()
    if not key or not key.startswith("sk-"):
        raise RuntimeError(
            f"Clave de OpenAI inválida en {API_KEY_PATH}. "
            "Debe contener solo una clave que empiece por 'sk-'."
        )
    return key


def build_openai_client():
    """Create an authenticated OpenAI client instance."""
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime env
        raise RuntimeError(
            "La librería 'openai' no está instalada. Ejecuta: pip install openai"
        ) from exc
    return OpenAI(api_key=load_api_key(), timeout=20.0)
