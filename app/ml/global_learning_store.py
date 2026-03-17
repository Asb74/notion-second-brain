"""Global reusable learning store for prompt examples across email modules."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_TRAINING_DATA_FILE = "training_data.json"
MAX_RECORDS_PER_TYPE = 500
MIN_USER_FINAL_LENGTH = 12
SUPPORTED_TYPES = ("email_summary", "email_reply", "email_classification")


class GlobalLearningStore:
    """Persist and retrieve feedback examples for multiple email learning datasets."""

    def __init__(self, file_path: str | Path = DEFAULT_TRAINING_DATA_FILE) -> None:
        self.file_path = Path(file_path)
        self._lock = threading.Lock()

    def guardar_feedback(
        self,
        tipo: str,
        input_text: str,
        ai_output: str,
        user_final: str,
        instrucciones: str,
    ) -> dict[str, Any]:
        dataset_type = str(tipo or "").strip().lower()
        if dataset_type not in SUPPORTED_TYPES:
            return {"saved": False, "reason": "invalid_type"}

        cleaned_input = self._normalize_text(input_text)
        cleaned_ai_output = self._normalize_text(ai_output)
        cleaned_user_final = self._normalize_text(user_final)
        cleaned_instructions = self._normalize_text(instrucciones)

        if not cleaned_input or not cleaned_ai_output or not cleaned_user_final:
            return {"saved": False, "reason": "empty"}
        if len(cleaned_user_final) < MIN_USER_FINAL_LENGTH:
            return {"saved": False, "reason": "too_short"}

        new_row = {
            "input": cleaned_input,
            "ai_output": cleaned_ai_output,
            "user_final": cleaned_user_final,
            "instructions": cleaned_instructions,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

        with self._lock:
            payload = self._load_payload()
            rows = payload.setdefault(dataset_type, [])
            if self._is_simple_duplicate(rows, new_row):
                return {"saved": False, "reason": "duplicate"}
            rows.append(new_row)
            payload[dataset_type] = rows[-MAX_RECORDS_PER_TYPE:]
            self._save_payload(payload)
            count = len(payload[dataset_type])

        return {"saved": True, "reason": "inserted", "count": count}

    def obtener_ejemplos(self, tipo: str, input_actual: str, max_ejemplos: int = 5) -> list[dict[str, str]]:
        del input_actual  # reservado para futura similitud semántica
        dataset_type = str(tipo or "").strip().lower()
        if dataset_type not in SUPPORTED_TYPES:
            return []

        limit = max(1, int(max_ejemplos))
        with self._lock:
            payload = self._load_payload()

        examples = payload.get(dataset_type, [])
        if not isinstance(examples, list):
            return []

        normalized: list[dict[str, str]] = []
        for item in examples:
            if not isinstance(item, dict):
                continue
            input_value = self._normalize_text(item.get("input"))
            user_final = self._normalize_text(item.get("user_final"))
            if not input_value or not user_final:
                continue
            normalized.append(
                {
                    "input": input_value,
                    "ai_output": self._normalize_text(item.get("ai_output")),
                    "user_final": user_final,
                    "instructions": self._normalize_text(item.get("instructions")),
                    "timestamp": str(item.get("timestamp") or "").strip(),
                }
            )
        return normalized[-limit:]

    def build_prompt_context(self, tipo: str, input_actual: str, max_ejemplos: int = 5) -> str:
        rows = self.obtener_ejemplos(tipo=tipo, input_actual=input_actual, max_ejemplos=max_ejemplos)
        if not rows:
            return ""

        chunks = ["Ejemplos previos correctos:"]
        for sample in rows:
            chunks.extend(
                [
                    "---",
                    "EMAIL:",
                    sample["input"],
                    "",
                    "RESPUESTA CORRECTA:",
                    sample["user_final"],
                ]
            )
        return "\n".join(chunks).strip()

    def _is_simple_duplicate(self, rows: list[dict[str, Any]], new_row: dict[str, str]) -> bool:
        new_signature = self._build_duplicate_signature(new_row)
        for item in rows[-30:]:
            if not isinstance(item, dict):
                continue
            if self._build_duplicate_signature(item) == new_signature:
                return True
        return False

    def _build_duplicate_signature(self, row: dict[str, Any]) -> str:
        return "||".join(
            [
                self._normalize_text(row.get("input")),
                self._normalize_text(row.get("ai_output")),
                self._normalize_text(row.get("user_final")),
                self._normalize_text(row.get("instructions")),
            ]
        )

    def _load_payload(self) -> dict[str, Any]:
        default_payload = {key: [] for key in SUPPORTED_TYPES}
        if not self.file_path.exists():
            return default_payload
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default_payload
        if not isinstance(payload, dict):
            return default_payload

        normalized_payload = dict(default_payload)
        for key in SUPPORTED_TYPES:
            rows = payload.get(key)
            normalized_payload[key] = rows if isinstance(rows, list) else []
        return normalized_payload

    def _save_payload(self, payload: dict[str, Any]) -> None:
        normalized = {key: payload.get(key, []) if isinstance(payload.get(key), list) else [] for key in SUPPORTED_TYPES}
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"(\*\*|__|##+)", "", text)
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
