"""Consolidation helpers for few-shot datasets."""

from __future__ import annotations

import re

from app.ml.dataset_state_service import DatasetStateService
from app.persistence.ml_training_repository import MLTrainingRepository

FEW_SHOT_DATASETS = {"email_summary", "email_response"}


class FewShotLearningService:
    def __init__(self, conn):
        self.repo = MLTrainingRepository(conn)
        self.dataset_state_service = DatasetStateService(conn)

    def consolidar_aprendizaje(self, dataset: str) -> dict[str, int]:
        normalized_dataset = (dataset or "").strip()
        if normalized_dataset not in FEW_SHOT_DATASETS:
            raise ValueError(f"Dataset no soportado para few-shot: {normalized_dataset}")

        consolidation = self.repo.consolidate_few_shot_dataset(normalized_dataset, clean_text=self.clean_markdown)
        self.dataset_state_service.mark_learning_updated(normalized_dataset, consolidation["total_valid"])
        return consolidation

    @staticmethod
    def clean_markdown(text: str) -> str:
        sanitized = re.sub(r"\*\*(.*?)\*\*", r"\1", text or "")
        sanitized = sanitized.replace("**", "")
        return sanitized.strip()
