"""Central orchestration for continuous/incremental learning."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.email.email_classifier import EmailClassifier
from app.ml.dataset_state_service import DatasetStateService
from app.ml.retraining_service import AUTO_TRAIN_THRESHOLDS, DatasetRetrainingService, MIN_TRAIN_INTERVAL_HOURS
from app.ml.training_example_service import TrainingExampleService

logger = logging.getLogger(__name__)


AUTO_FULL_RETRAIN_RULES: dict[str, dict[str, int]] = {
    "email_classification": {
        "min_examples": 10,
        "min_distinct_labels": 2,
        "pending_examples_threshold": 10,
        "cooldown_minutes": 30,
    }
}


class ContinuousLearningService:
    def __init__(self, db_connection: sqlite3.Connection, email_classifier: EmailClassifier | None = None):
        self.conn = db_connection
        self.dataset_state_service = DatasetStateService(db_connection)
        self.example_service = TrainingExampleService(db_connection)
        self.email_classifier = email_classifier
        self.retraining_service = DatasetRetrainingService(db_connection, getattr(email_classifier, "email_repo", None))

    def on_new_training_example(
        self,
        dataset: str,
        input_text: str,
        output_text: str | None,
        label: str | None,
        metadata: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        dataset_name = (dataset or "").strip()
        save_result = self.example_service.save_training_example_if_new(
            dataset=dataset_name,
            input_text=input_text,
            output_text=output_text,
            label=label,
            metadata=metadata,
            source=source or "manual",
            detect_near_duplicates=dataset_name in {"email_response", "email_summary"},
        )
        if not bool(save_result.get("inserted")):
            logger.info("Ejemplo duplicado ignorado: dataset=%s", dataset_name)
            return {
                "ok": True,
                "dataset": dataset_name,
                "inserted": False,
                "reason": str(save_result.get("reason") or "duplicate"),
                "incremental": {"trained": False, "reason": "skip_duplicate"},
                "full_retrain": {"trained": False, "reason": "skip_duplicate"},
            }

        state_after_insert = self.dataset_state_service.get_state(dataset_name)
        pending_examples = int(state_after_insert["pending_examples_count"] or 0) if state_after_insert is not None else 0
        auto_threshold = int(AUTO_TRAIN_THRESHOLDS.get(dataset_name, 0) or 0)

        incremental_result = {"trained": False, "reason": "not_applicable"}
        if dataset_name == "email_classification":
            incremental_result = self.maybe_incremental_train_email_classification(label=label, input_text=input_text)
        else:
            logger.info("Dataset %s marcado dirty sin entrenamiento automático", dataset_name)

        full_retrain_result = self.maybe_full_retrain(dataset_name, force=False)

        return {
            "ok": True,
            "dataset": dataset_name,
            "inserted": True,
            "reason": str(save_result.get("reason") or "inserted"),
            "pending_examples": pending_examples,
            "auto_train_threshold": auto_threshold,
            "incremental": incremental_result,
            "full_retrain": full_retrain_result,
        }

    def maybe_incremental_train_email_classification(self, label: str | None, input_text: str | None = None) -> dict[str, Any]:
        classifier = self.email_classifier
        if classifier is None:
            return {"trained": False, "reason": "classifier_not_available"}

        if not classifier.can_incremental_train(new_label=label):
            reason = classifier.last_training_warning or "not_ready_for_incremental"
            if "no entrenado" in reason.lower():
                logger.warning("Incremental training omitido: modelo no entrenado")
            else:
                logger.info("Incremental training omitido: %s", reason)
            return {"trained": False, "reason": reason}

        subject = ""
        body = ""
        if input_text:
            first, sep, rest = input_text.partition("\n")
            subject = first
            body = rest if sep else ""

        trained = classifier.incremental_train_single(subject=subject, sender="", body_text=body, label=str(label or "other"))
        if trained:
            self.dataset_state_service.mark_incremental_success("email_classification")
            logger.info("Incremental training aplicado correctamente")
            return {"trained": True, "reason": "incremental_ok"}

        self.dataset_state_service.mark_error("email_classification", classifier.last_training_warning or "Incremental training failed")
        return {"trained": False, "reason": classifier.last_training_warning or "incremental_failed"}

    def maybe_full_retrain(self, dataset: str, force: bool = False) -> dict[str, Any]:
        dataset_name = (dataset or "").strip()
        rules = AUTO_FULL_RETRAIN_RULES.get(dataset_name)
        if not rules and dataset_name not in AUTO_TRAIN_THRESHOLDS:
            return {"trained": False, "reason": "dataset_without_auto_rules"}

        state = self.dataset_state_service.get_state(dataset_name)
        if state is None:
            return {"trained": False, "reason": "state_not_found"}

        if not bool(state["auto_learning_enabled"]):
            return {"trained": False, "reason": "auto_learning_disabled"}
        if not bool(state["dirty"]):
            return {"trained": False, "reason": "dataset_clean"}

        examples_count = int(state["examples_count"] or 0)
        pending = int(state["pending_examples_count"] or 0)
        distinct_labels = self._count_distinct_labels(dataset_name)

        if rules:
            if examples_count < int(rules["min_examples"]):
                logger.info("Full retrain omitido: ejemplos insuficientes")
                return {"trained": False, "reason": "insufficient_examples"}
            if distinct_labels < int(rules["min_distinct_labels"]):
                logger.info("Full retrain omitido: etiquetas insuficientes")
                return {"trained": False, "reason": "insufficient_labels"}

        pending_threshold = int(AUTO_TRAIN_THRESHOLDS.get(dataset_name, int(rules["pending_examples_threshold"] if rules else 0)))
        if not force:
            if pending < pending_threshold:
                logger.info("Full retrain omitido: threshold pendiente no alcanzado")
                return {"trained": False, "reason": "pending_threshold_not_reached"}
            if self._is_cooldown_active(str(state["last_trained_at"] or ""), MIN_TRAIN_INTERVAL_HOURS * 60):
                logger.info("Full retrain omitido: cooldown activo")
                return {"trained": False, "reason": "cooldown_active"}

        if not force:
            self.dataset_state_service.mark_auto_training_scheduled(dataset_name)
            scheduled = self.retraining_service.start_auto_training_in_background(dataset_name, classifier=self.email_classifier)
            return {
                "trained": False,
                "reason": str(scheduled.get("reason") or "auto_training_not_scheduled"),
                "scheduled": bool(scheduled.get("scheduled")),
                "pending_examples": pending,
                "auto_train_threshold": pending_threshold,
            }

        result = self.retraining_service.check_and_retrain_dataset(dataset_name, auto=False, classifier=self.email_classifier)
        return {
            "trained": bool(result.get("trained")),
            "reason": str(result.get("reason") or "full_retrain_failed"),
            "scheduled": False,
        }

    def _count_distinct_labels(self, dataset: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(DISTINCT LOWER(TRIM(label))) AS total
            FROM ml_training_examples
            WHERE dataset = ? AND TRIM(COALESCE(label, '')) != ''
            """,
            (dataset,),
        ).fetchone()
        return int(row["total"] if row else 0)

    @staticmethod
    def _is_cooldown_active(last_trained_at: str, cooldown_minutes: int) -> bool:
        if not last_trained_at:
            return False
        try:
            trained_at = datetime.fromisoformat(last_trained_at)
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        if trained_at.tzinfo is None:
            trained_at = trained_at.replace(tzinfo=timezone.utc)
        return now < trained_at + timedelta(minutes=max(0, cooldown_minutes))
