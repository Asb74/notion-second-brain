"""JSONL dataset manager for continuous ML training examples."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DATASETS = {
    "email_classification",
    "email_summary",
    "attachment_summary",
    "email_reply",
}


@dataclass(slots=True)
class DatasetStats:
    total_examples: int = 0
    unique_examples: int = 0
    pending_retraining_examples: int = 0
    last_updated: str = ""


class MLTrainingManager:
    """Manage JSONL training datasets with deduplication and retraining triggers."""

    def __init__(
        self,
        base_dir: str | Path,
        retrain_threshold: int = 20,
        datasets: set[str] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.retrain_threshold = max(1, int(retrain_threshold))
        self.datasets = set(datasets or DEFAULT_DATASETS)
        self._state_file = self.base_dir / "_dataset_state.json"
        self._state = self._load_state()

    def register_dataset(self, dataset_name: str) -> None:
        normalized = self._normalize_dataset(dataset_name)
        if not normalized:
            raise ValueError("Dataset name must not be empty")
        self.datasets.add(normalized)

    def save_training_example(
        self,
        *,
        dataset: str,
        input_text: str,
        output_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_dataset = self._normalize_dataset(dataset)
        if not normalized_dataset:
            raise ValueError("Dataset name must not be empty")
        self.datasets.add(normalized_dataset)

        normalized_input = str(input_text or "").strip()
        normalized_output = str(output_text or "").strip()
        if not normalized_input or not normalized_output:
            raise ValueError("input_text and output_text are required")

        fingerprint = self._build_hash(normalized_input, normalized_output)
        if self.deduplicate_example(dataset=normalized_dataset, sample_hash=fingerprint):
            logger.info("[ML] Duplicate skipped")
            return {"saved": False, "reason": "duplicate", "hash": fingerprint}

        timestamp = datetime.now(timezone.utc).isoformat()
        payload = {
            "input_text": normalized_input,
            "output_text": normalized_output,
            "metadata": {
                "timestamp": timestamp,
                "source": "user_confirmation",
                **(metadata or {}),
            },
            "hash": fingerprint,
        }
        dataset_file = self._dataset_file(normalized_dataset)
        with dataset_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        self._ensure_dataset_state(normalized_dataset)
        self._state[normalized_dataset]["pending_retraining_examples"] += 1
        self._state[normalized_dataset]["last_updated"] = timestamp
        self._save_state()

        logger.info("[ML] Training example saved")
        retraining = self.trigger_retraining(normalized_dataset)
        return {
            "saved": True,
            "reason": "inserted",
            "hash": fingerprint,
            "retraining_triggered": retraining,
        }

    def deduplicate_example(self, *, dataset: str, sample_hash: str) -> bool:
        dataset_file = self._dataset_file(dataset)
        if not dataset_file.exists():
            return False
        with dataset_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = self._parse_jsonl_line(line)
                if not record:
                    continue
                if str(record.get("hash") or "") == sample_hash:
                    return True
        return False

    def trigger_retraining(self, dataset: str) -> bool:
        normalized_dataset = self._normalize_dataset(dataset)
        self._ensure_dataset_state(normalized_dataset)
        pending = int(self._state[normalized_dataset]["pending_retraining_examples"])
        if pending < self.retrain_threshold:
            return False

        logger.info("[ML] Retraining triggered")
        logger.info("Dataset updated. Retraining model...")
        self._state[normalized_dataset]["pending_retraining_examples"] = 0
        self._state[normalized_dataset]["last_retraining_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return True

    def dataset_stats(self, dataset: str) -> DatasetStats:
        normalized_dataset = self._normalize_dataset(dataset)
        self._ensure_dataset_state(normalized_dataset)
        total = 0
        unique_hashes: set[str] = set()

        dataset_file = self._dataset_file(normalized_dataset)
        if dataset_file.exists():
            with dataset_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    record = self._parse_jsonl_line(line)
                    if not record:
                        continue
                    total += 1
                    record_hash = str(record.get("hash") or "")
                    if record_hash:
                        unique_hashes.add(record_hash)

        return DatasetStats(
            total_examples=total,
            unique_examples=len(unique_hashes),
            pending_retraining_examples=int(self._state[normalized_dataset]["pending_retraining_examples"]),
            last_updated=str(self._state[normalized_dataset].get("last_updated") or ""),
        )

    @staticmethod
    def _build_hash(input_text: str, output_text: str) -> str:
        return sha256(f"{input_text}{output_text}".encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_dataset(dataset: str) -> str:
        return str(dataset or "").strip().lower()

    def _dataset_file(self, dataset: str) -> Path:
        return self.base_dir / f"{self._normalize_dataset(dataset)}.jsonl"

    @staticmethod
    def _parse_jsonl_line(line: str) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped:
            return None
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None
        return loaded

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self._state_file.exists():
            return {}
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            normalized[self._normalize_dataset(str(key))] = value
        return normalized

    def _save_state(self) -> None:
        self._state_file.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_dataset_state(self, dataset: str) -> None:
        normalized_dataset = self._normalize_dataset(dataset)
        if normalized_dataset not in self._state:
            self._state[normalized_dataset] = {
                "pending_retraining_examples": 0,
                "last_updated": "",
                "last_retraining_at": "",
            }
