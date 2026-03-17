"""Persistence and prompt-context helpers for email summary feedback learning."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_FEEDBACK_FILE = "training_data_email_summary.json"
MIN_SUMMARY_LENGTH = 12


class EmailSummaryFeedbackStore:
    """Store confirmed summary feedback and expose recent examples for prompting."""

    def __init__(self, file_path: str | Path = DEFAULT_FEEDBACK_FILE) -> None:
        self.file_path = Path(file_path)
        self._lock = threading.Lock()

    def guardar_feedback(
        self,
        email: str,
        ai_output: str,
        user_final: str,
        instrucciones: str,
    ) -> dict[str, Any]:
        input_email = str(email or "").strip()
        ai_summary = str(ai_output or "").strip()
        user_summary = str(user_final or "").strip()
        refinement_instructions = str(instrucciones or "").strip()

        if not input_email or not ai_summary or not user_summary:
            return {"saved": False, "reason": "empty"}
        if len(user_summary) < MIN_SUMMARY_LENGTH:
            return {"saved": False, "reason": "too_short"}

        sample = {
            "input_email": input_email,
            "ai_output": ai_summary,
            "user_final": user_summary,
            "refinement_instructions": refinement_instructions,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            payload = self._load_payload()
            samples = payload.setdefault("samples", [])
            samples.append(sample)
            self._save_payload(payload)
        return {"saved": True, "reason": "inserted", "count": len(payload["samples"])}

    def load_recent_examples(self, limit: int = 5) -> list[dict[str, str]]:
        max_items = max(1, int(limit))
        with self._lock:
            payload = self._load_payload()
        raw_samples = payload.get("samples", [])
        if not isinstance(raw_samples, list):
            return []

        valid_rows: list[dict[str, str]] = []
        for item in raw_samples:
            if not isinstance(item, dict):
                continue
            input_email = str(item.get("input_email") or "").strip()
            user_final = str(item.get("user_final") or "").strip()
            if not input_email or not user_final:
                continue
            valid_rows.append(
                {
                    "input_email": input_email,
                    "ai_output": str(item.get("ai_output") or "").strip(),
                    "user_final": user_final,
                    "refinement_instructions": str(item.get("refinement_instructions") or "").strip(),
                    "timestamp": str(item.get("timestamp") or "").strip(),
                }
            )
        return valid_rows[-max_items:]

    def build_prompt_context(self, limit: int = 5) -> str:
        rows = self.load_recent_examples(limit=limit)
        if not rows:
            return ""
        chunks: list[str] = ["Ejemplos previos de resúmenes correctos:"]
        for index, sample in enumerate(rows, start=1):
            chunks.append(
                f"{index}. Email: {sample['input_email']}\n"
                f"   Resumen IA: {sample['ai_output']}\n"
                f"   Resumen correcto: {sample['user_final']}"
            )
        return "\n\n".join(chunks)

    def _load_payload(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {"samples": []}
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"samples": []}
        if not isinstance(payload, dict):
            return {"samples": []}
        samples = payload.get("samples")
        if not isinstance(samples, list):
            payload["samples"] = []
        return payload

    def _save_payload(self, payload: dict[str, Any]) -> None:
        normalized = payload if isinstance(payload, dict) else {"samples": []}
        if not isinstance(normalized.get("samples"), list):
            normalized["samples"] = []
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
