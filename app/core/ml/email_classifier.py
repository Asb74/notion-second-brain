"""Runtime email classification service backed by persisted ML artifacts."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class EmailClassifier:
    """Load and serve the latest trained email classification model."""

    def __init__(self) -> None:
        self.model = None
        self.vectorizer = None
        self.model_path = Path("models/email_classification_model.pkl")
        self.vectorizer_path = Path("models/email_vectorizer.pkl")
        self._load_model()

    def _load_model(self) -> None:
        try:
            import joblib

            self.model = joblib.load(self.model_path)
            self.vectorizer = joblib.load(self.vectorizer_path)
            logger.info("Modelo de clasificación cargado correctamente")
        except Exception:  # noqa: BLE001
            logger.warning("Modelo no disponible, usando fallback")
            self.model = None
            self.vectorizer = None

    def reload_model(self) -> None:
        self._load_model()

    def is_ready(self) -> bool:
        return self.model is not None and self.vectorizer is not None

    def predict(self, subject: str, body: str) -> tuple[str, float]:
        if not self.is_ready():
            return "other", 0.0

        text = f"{subject} {body}".strip()
        features = self.vectorizer.transform([text])
        predicted = str(self.model.predict(features)[0])

        if hasattr(self.model, "predict_proba"):
            probabilities = self.model.predict_proba(features)
            confidence = float(max(probabilities[0])) if len(probabilities) else 0.0
        else:
            confidence = 1.0

        logger.info("ML clasificación → %s (%.2f)", predicted, confidence)
        return predicted, confidence
