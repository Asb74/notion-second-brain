"""Backward-compatible wrapper around the global learning store for email summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ml.global_learning_store import GlobalLearningStore

DEFAULT_FEEDBACK_FILE = "training_data.json"


class EmailSummaryFeedbackStore:
    """Compatibility adapter that stores summary feedback in the shared training file."""

    def __init__(self, file_path: str | Path = DEFAULT_FEEDBACK_FILE) -> None:
        self._store = GlobalLearningStore(file_path=file_path)

    def guardar_feedback(
        self,
        email: str,
        ai_output: str,
        user_final: str,
        instrucciones: str,
    ) -> dict[str, Any]:
        return self._store.guardar_feedback(
            tipo="email_summary",
            input_text=email,
            ai_output=ai_output,
            user_final=user_final,
            instrucciones=instrucciones,
        )

    def load_recent_examples(self, limit: int = 5) -> list[dict[str, str]]:
        return self._store.obtener_ejemplos(tipo="email_summary", input_actual="", max_ejemplos=limit)

    def build_prompt_context(self, limit: int = 5) -> str:
        return self._store.build_prompt_context(tipo="email_summary", input_actual="", max_ejemplos=limit)
