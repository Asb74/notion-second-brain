"""Hybrid email classifier (rules + trainable local ML)."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.email.ml_email_model import MLEmailModel


class EmailClassifier:
    """Classify emails into the 5 fixed tabs using rules and optional ML fallback."""

    RULE_THRESHOLD = 3
    MIN_TRAINING_SAMPLES = 30

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
        self.ml_model = MLEmailModel(model_path=model_path)
        self.examples_count = 0
        self.ml_model.load()
        if self.email_repo is not None:
            self.retrain_if_possible(force=False)

    def classify(self, subject: str | None, sender: str | None, body_text: str | None) -> str:
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
        ml_prediction = self.ml_model.predict(ml_features)
        if ml_prediction:
            return ml_prediction

        if self._is_internal_sender(sender_text):
            return "priority"
        return "other"

    def retrain_if_possible(self, force: bool = False) -> bool:
        if self.email_repo is None:
            return False

        dataset = self.email_repo.get_labeled_dataset()
        self.examples_count = len(dataset)
        if not force and self.examples_count < self.MIN_TRAINING_SAMPLES:
            return False
        if not dataset:
            return False

        texts = [MLEmailModel.compose_features(row["subject"], row["sender"], row["body_text"]) for row in dataset]
        labels = [str(row["label"] or "other") for row in dataset]

        self.ml_model = MLEmailModel(model_path=self.ml_model.model_path)
        self.ml_model.fit(texts, labels)
        self.ml_model.save()
        return True

    def model_status(self) -> str:
        if self.ml_model.is_trained and self.examples_count >= self.MIN_TRAINING_SAMPLES:
            return f"Modelo: híbrido ({self.examples_count} ejemplos)"
        return "Modelo: reglas"

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
