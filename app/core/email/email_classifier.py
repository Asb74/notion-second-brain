"""Hybrid email classifier (rules + trainable local ML)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.core.email.ml_email_model import MLEmailModel

logger = logging.getLogger(__name__)


class EmailClassifier:
    """Classify emails with rules and optional ML fallback using dynamic categories."""

    RULE_THRESHOLD = 3
    ML_CONFIDENCE_THRESHOLD = 0.55
    MIN_TRAINING_SAMPLES = 10

    ORDER_PATTERNS = [
        r"\bpedido\b",
        r"orden de carga",
        r"\balbar[áa]n\b",
        r"\border\b",
        r"\boc\b",
        r"\bcarga\b",
        r"pedido\s*10\d{4,}",
    ]
    SUBSCRIPTION_PATTERNS = [
        r"newsletter",
        r"suscripci[oó]n",
        r"confirmar suscripci[oó]n",
        r"verifica tu correo",
        r"\bwelcome\b",
        r"bolet[íi]n",
        r"unsubscribe",
    ]
    MARKETING_PATTERNS = [
        r"oferta",
        r"promo",
        r"venta privada",
        r"descuento",
        r"black friday",
        r"marketing",
    ]
    PRIORITY_HINTS = [
        r"incidencia",
        r"anal[íi]tica",
        r"reclamaci[oó]n",
        r"urgente",
        r"falta",
        r"problema",
        r"calidad",
        r"factura",
        r"transporte",
        r"petici[oó]n de precio",
        r"presupuesto",
    ]
    INFO_PATTERNS = [r"informativo", r"aviso general", r"sin acci[oó]n"]

    def __init__(self, email_repo=None, model_path: str | Path = "app/secrets/email_model.joblib"):
        self.email_repo = email_repo
        self.model_path = Path(model_path)
        self.ml_model = MLEmailModel(model_path=model_path)
        self.examples_count = 0
        self.categories_count = 0
        self.last_training_warning: str | None = None
        self._known_categories: list[str] = []
        self.ml_model.load()
        categories = self._sync_categories_state()
        self.categories_count = len(categories)
        if self.email_repo is not None:
            self.retrain_if_possible(force=False)

    def classify(
        self,
        subject: str | None,
        sender: str | None,
        body_text: str | None,
        previous_type: str | None = None,
    ) -> str:
        subject_text = (subject or "").lower()
        sender_text = (sender or "").lower()
        body = (body_text or "")

        if self.email_repo is not None:
            forced = self.email_repo.find_forced_label_for_sender(sender_text)
            if forced:
                return forced

        rule_result = self._classify_by_rules(subject_text, sender_text)
        if rule_result is not None:
            return rule_result

        ml_features = MLEmailModel.compose_features(subject, sender, body)
        ml_prediction, confidence = self.ml_model.predict_with_confidence(ml_features)
        if ml_prediction and confidence < self.ML_CONFIDENCE_THRESHOLD and previous_type:
            return previous_type
        if ml_prediction:
            return ml_prediction

        if self._is_internal_sender(sender_text):
            return "priority"
        return "other"

    def retrain_if_possible(self, force: bool = False) -> bool:
        if self.email_repo is None:
            self.last_training_warning = "Repositorio de emails no disponible."
            logger.warning("Entrenamiento cancelado: email_repo no disponible")
            return False

        previous_categories = list(self._known_categories)
        categories = self._available_categories()
        self._known_categories = list(categories)
        category_changed = categories != previous_categories
        self.categories_count = len(categories)

        dataset = self.email_repo.get_labeled_dataset()
        self.examples_count = len(dataset)
        self.last_training_warning = None
        logger.info(
            "Entrenamiento email classifier: ejemplos=%s categorias=%s force=%s category_changed=%s",
            self.examples_count,
            categories,
            force,
            category_changed,
        )

        if not dataset:
            self.last_training_warning = "No hay ejemplos etiquetados para entrenar."
            logger.warning("Entrenamiento cancelado: dataset vacío")
            return False

        if self.examples_count < self.MIN_TRAINING_SAMPLES:
            self.last_training_warning = (
                f"Entrenamiento cancelado: ejemplos insuficientes ({self.examples_count} < {self.MIN_TRAINING_SAMPLES})."
            )
            logger.warning(self.last_training_warning)
            return False

        texts = [MLEmailModel.compose_features(row["subject"], row["sender"], row["body_text"]) for row in dataset]
        labels = [str(row["label"] or "other") for row in dataset]

        unique_labels = {label for label in labels}
        logger.info("Entrenamiento email classifier: etiquetas detectadas=%s", sorted(unique_labels))
        if len(unique_labels) == 1:
            self.last_training_warning = "Entrenamiento cancelado: solo una categoría activa."
            logger.warning(self.last_training_warning)
            return False

        try:
            if category_changed or not self.ml_model.is_trained:
                logger.info("Entrenamiento email classifier: ejecutando fit completo")
                candidate_model = MLEmailModel(model_path=self.ml_model.model_path)
                candidate_model.fit(texts, labels, classes=categories)
                if candidate_model.is_trained:
                    self.ml_model = candidate_model
            else:
                logger.info("Entrenamiento email classifier: ejecutando partial_fit")
                self.ml_model.partial_fit(texts, labels)
        except Exception as exc:  # noqa: BLE001
            self.last_training_warning = f"Error durante entrenamiento: {exc}"
            logger.exception("Entrenamiento cancelado por excepción")
            return False

        if self.ml_model.last_warning:
            self.last_training_warning = self.ml_model.last_warning
            logger.warning("Entrenamiento cancelado por warning del modelo: %s", self.ml_model.last_warning)
            return False

        if not self.ml_model.is_trained:
            self.last_training_warning = "El modelo no quedó entrenado tras el intento de fit."
            logger.warning("Entrenamiento cancelado: modelo no entrenado")
            return False

        self.ml_model.save()
        logger.info("Entrenamiento email classifier completado correctamente")
        return True


    def can_incremental_train(self, new_label: str | None = None) -> bool:
        categories = self._sync_categories_state()
        self.categories_count = len(categories)
        normalized_label = (new_label or "").strip()

        if not self.ml_model.is_trained:
            self.last_training_warning = "Incremental training omitido: modelo no entrenado"
            logger.warning("Incremental training omitido: modelo no entrenado")
            return False

        if len(categories) < 2:
            self.last_training_warning = "Incremental training omitido: solo una categoría"
            logger.warning("Incremental training omitido: solo una categoría")
            return False

        if not normalized_label:
            self.last_training_warning = "Incremental training omitido: etiqueta inválida"
            logger.warning("Incremental training omitido: etiqueta inválida")
            return False

        if normalized_label not in categories:
            self.last_training_warning = f"Incremental training omitido: etiqueta desconocida ({normalized_label})"
            logger.warning("Incremental training omitido: etiqueta desconocida (%s)", normalized_label)
            return False

        if self.ml_model.last_warning:
            self.last_training_warning = f"Incremental training omitido por warning previo: {self.ml_model.last_warning}"
            logger.warning("Incremental training omitido por warning previo: %s", self.ml_model.last_warning)
            return False

        return True

    def incremental_train_on_examples(self, texts: list[str], labels: list[str]) -> bool:
        if not texts or not labels or len(texts) != len(labels):
            self.last_training_warning = "Incremental training omitido: ejemplos inválidos."
            return False

        normalized_labels = [str(label or "").strip() for label in labels]
        if any(not label for label in normalized_labels):
            self.last_training_warning = "Incremental training omitido: etiqueta vacía."
            return False

        if any(not self.can_incremental_train(new_label=label) for label in normalized_labels):
            return False

        try:
            features = self.ml_model.vectorizer.transform(texts)
            self.ml_model.classifier.partial_fit(features, normalized_labels)
            self.ml_model.last_warning = None
            self.ml_model.save()
            self.last_training_warning = None
            logger.info("Incremental training aplicado correctamente")
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_training_warning = f"Error en incremental training: {exc}"
            logger.exception("Error en incremental training")
            return False

    def incremental_train_single(self, subject: str, sender: str, body_text: str, label: str) -> bool:
        logger.info("Incremental training email_classification: label=%s", label)
        if not self.can_incremental_train(new_label=label):
            return False

        features_text = MLEmailModel.compose_features(subject, sender, body_text)
        return self.incremental_train_on_examples([features_text], [label])

    def reclassify_all_emails(self) -> int:
        if self.email_repo is None:
            return 0

        rows = self.email_repo.get_all_emails_for_classification(exclude_user_labeled=True)
        updates: list[tuple[str, str]] = []
        for row in rows:
            predicted = self.classify(
                subject=row["subject"],
                sender=row["sender"],
                body_text=row["body_text"],
                previous_type=row["type"],
            )
            updates.append((str(row["gmail_id"]), predicted))
        self.email_repo.bulk_update_email_types(updates)
        return len(updates)

    def model_status(self) -> str:
        if self.ml_model.is_trained:
            base = f"Modelo: híbrido ({self.examples_count} ejemplos, {self.categories_count} categorías)"
            if self.last_training_warning:
                return f"{base} | aviso: {self.last_training_warning}"
            return base
        if self.last_training_warning:
            return f"Modelo: reglas ({self.categories_count} categorías) | aviso: {self.last_training_warning}"
        return f"Modelo: reglas ({self.categories_count} categorías)"

    def _sync_categories_state(self) -> list[str]:
        categories = self._available_categories()
        if categories != self._known_categories:
            self._known_categories = categories
        return categories

    def _available_categories(self) -> list[str]:
        if self.email_repo is None:
            return list(MLEmailModel.DEFAULT_CLASSES)
        names = self.email_repo.get_category_names()
        return names or list(MLEmailModel.DEFAULT_CLASSES)

    def _classify_by_rules(self, subject: str, sender: str) -> str | None:
        scores = {"priority": 0, "order": 0, "subscription": 0, "marketing": 0, "other": 0}

        scores["order"] += self._score_matches(subject, self.ORDER_PATTERNS, weight=3)
        scores["subscription"] += self._score_matches(subject, self.SUBSCRIPTION_PATTERNS, weight=3)
        scores["marketing"] += self._score_matches(subject, self.MARKETING_PATTERNS, weight=2)
        scores["priority"] += self._score_matches(subject, self.PRIORITY_HINTS, weight=3)
        scores["other"] += self._score_matches(subject, self.INFO_PATTERNS, weight=2)

        internal = self._is_internal_sender(sender)
        if internal:
            scores["priority"] += 2

        strongest_label = max(scores, key=scores.get)
        strongest_score = scores[strongest_label]
        top_labels = [label for label, value in scores.items() if value == strongest_score and value > 0]

        if internal and strongest_label not in {"order", "subscription", "marketing"}:
            return "priority"

        if strongest_score >= self.RULE_THRESHOLD and len(top_labels) == 1:
            return strongest_label

        return None

    @staticmethod
    def _score_matches(text: str, patterns: list[str], weight: int) -> int:
        score = 0
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                score += weight
        return score

    @staticmethod
    def _is_internal_sender(sender: str) -> bool:
        return "@sansebas.es" in sender
