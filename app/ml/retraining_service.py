"""Reusable dataset retraining orchestration."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from app.core.email.email_classifier import EmailClassifier
from app.ml.dataset_state_service import DatasetStateService

logger = logging.getLogger(__name__)


AUTO_TRAIN_THRESHOLDS = {
    "email_classification": 30,
}
MIN_TRAIN_INTERVAL_HOURS = 6


class DatasetRetrainingService:
    _training_lock = threading.Lock()
    training_in_progress = False

    def __init__(self, conn: sqlite3.Connection, email_repo) -> None:
        self.conn = conn
        self.email_repo = email_repo
        self.state_service = DatasetStateService(conn)

    def check_and_retrain_dataset(self, dataset_name: str, auto: bool = False, classifier: EmailClassifier | None = None) -> dict[str, str | bool]:
        dataset = (dataset_name or "").strip()
        if DatasetRetrainingService.training_in_progress:
            return {"trained": False, "reason": "Ya hay un entrenamiento en curso."}

        with DatasetRetrainingService._training_lock:
            if DatasetRetrainingService.training_in_progress:
                return {"trained": False, "reason": "Ya hay un entrenamiento en curso."}
            DatasetRetrainingService.training_in_progress = True

        started_at = time.perf_counter()
        self.state_service.mark_training_started(dataset)
        logger.info("Entrenamiento iniciado: dataset=%s auto=%s", dataset, auto)

        try:
            if dataset != "email_classification":
                return {"trained": False, "reason": "Dataset sin entrenamiento clásico."}

            state = self.state_service.get_state(dataset)
            if auto:
                if state is not None and not bool(state["dirty"]):
                    return {"trained": False, "reason": "Dataset sin cambios pendientes."}
                if not self.state_service.has_enough_new_examples(dataset, min_new_examples=5):
                    return {"trained": False, "reason": "Auto-retrain omitido: pocos ejemplos nuevos desde el último entrenamiento."}

            active_classifier = classifier or EmailClassifier(email_repo=self.email_repo)
            trained = active_classifier.retrain_if_possible(force=not auto)
            duration = round(time.perf_counter() - started_at, 3)
            if trained:
                self.state_service.set_examples_count(dataset, active_classifier.examples_count)
                self.state_service.mark_full_train_success(dataset)
                self.state_service.update_training_metrics(
                    dataset,
                    duration_seconds=duration,
                    trained_examples=active_classifier.examples_count,
                    precision=None,
                )
                logger.info(
                    "Entrenamiento completado: dataset=%s examples=%s duration_s=%.3f",
                    dataset,
                    active_classifier.examples_count,
                    duration,
                )
                return {"trained": True, "reason": "Reentrenamiento completado."}

            reason = active_classifier.last_training_warning or "No se pudo reentrenar el clasificador."
            self.state_service.mark_error(dataset, reason)
            logger.warning("Fallo en reentrenamiento de %s: %s", dataset, reason)
            return {"trained": False, "reason": reason}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallo en reentrenamiento de %s", dataset)
            self.state_service.mark_error(dataset, str(exc))
            return {"trained": False, "reason": str(exc)}
        finally:
            DatasetRetrainingService.training_in_progress = False

    def should_schedule_auto_training(self, dataset_name: str) -> bool:
        dataset = (dataset_name or "").strip()
        threshold = int(AUTO_TRAIN_THRESHOLDS.get(dataset, 0) or 0)
        if threshold <= 0:
            return False

        state = self.state_service.get_state(dataset)
        if state is None:
            return False
        pending = int(state["pending_examples_count"] or 0)
        if pending < threshold:
            return False

        last_trained_at = str(state["last_trained_at"] or "")
        if not last_trained_at:
            return True
        try:
            trained_dt = datetime.fromisoformat(last_trained_at)
        except ValueError:
            return True
        if trained_dt.tzinfo is None:
            trained_dt = trained_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= trained_dt + timedelta(hours=MIN_TRAIN_INTERVAL_HOURS)

    def start_auto_training_in_background(self, dataset_name: str, classifier: EmailClassifier | None = None) -> dict[str, str | bool]:
        dataset = (dataset_name or "").strip()
        if not self.should_schedule_auto_training(dataset):
            return {"scheduled": False, "reason": "threshold_or_interval_not_met"}
        if DatasetRetrainingService.training_in_progress:
            return {"scheduled": False, "reason": "training_in_progress"}

        self.state_service.mark_auto_training_scheduled(dataset)

        def worker() -> None:
            logger.info("Entrenamiento automático iniciado: dataset=%s", dataset)
            self.check_and_retrain_dataset(dataset, auto=True, classifier=classifier)

        threading.Thread(target=worker, daemon=True).start()
        return {"scheduled": True, "reason": "auto_training_scheduled"}
