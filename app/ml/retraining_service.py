"""Reusable dataset retraining orchestration."""

from __future__ import annotations

import logging
import sqlite3

from app.core.email.email_classifier import EmailClassifier
from app.ml.dataset_state_service import DatasetStateService

logger = logging.getLogger(__name__)


class DatasetRetrainingService:
    def __init__(self, conn: sqlite3.Connection, email_repo) -> None:
        self.conn = conn
        self.email_repo = email_repo
        self.state_service = DatasetStateService(conn)

    def check_and_retrain_dataset(self, dataset_name: str, auto: bool = False, classifier: EmailClassifier | None = None) -> dict[str, str | bool]:
        dataset = (dataset_name or "").strip()
        if dataset != "email_classification":
            return {"trained": False, "reason": f"Reentrenamiento para {dataset} pendiente de integración."}

        state = self.state_service.get_state(dataset)
        if auto:
            if state is not None and not bool(state["dirty"]):
                return {"trained": False, "reason": "Dataset sin cambios pendientes."}
            if not self.state_service.has_enough_new_examples(dataset, min_new_examples=5):
                return {"trained": False, "reason": "Auto-retrain omitido: pocos ejemplos nuevos desde el último entrenamiento."}

        active_classifier = classifier or EmailClassifier(email_repo=self.email_repo)
        trained = active_classifier.retrain_if_possible(force=not auto)
        if trained:
            self.state_service.mark_trained(dataset, examples_count=active_classifier.examples_count)
            logger.info("Dataset %s reentrenado correctamente", dataset)
            return {"trained": True, "reason": "Reentrenamiento completado."}

        reason = active_classifier.last_training_warning or "No se pudo reentrenar el clasificador."
        self.state_service.mark_error(dataset, reason)
        logger.warning("Fallo en reentrenamiento de %s: %s", dataset, reason)
        return {"trained": False, "reason": reason}
