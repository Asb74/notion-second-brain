"""Incremental ML model for email classification."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

try:
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
except Exception:  # noqa: BLE001
    joblib = None
    TfidfVectorizer = None
    SGDClassifier = None


logger = logging.getLogger(__name__)


class MLEmailModel:
    """Encapsulates a lightweight incremental text classifier."""

    DEFAULT_CLASSES = ["priority", "order", "subscription", "marketing", "other"]

    def __init__(self, model_path: str | Path):
        self.model_path = Path(model_path)
        self.is_available = bool(joblib and TfidfVectorizer and SGDClassifier)
        self.is_trained = False
        self.last_warning: str | None = None
        self.vectorizer = None
        self.classifier = None

    def _ensure_model_components(self) -> bool:
        if not self.is_available:
            return False
        if self.classifier is None:
            self.classifier = SGDClassifier(loss="log_loss", random_state=42)
        if self.vectorizer is None:
            self.vectorizer = TfidfVectorizer(ngram_range=(1, 2))
        return True

    def fit(self, texts: Sequence[str], labels: Sequence[str], classes: Sequence[str] | None = None) -> None:
        if not texts:
            self.is_trained = False
            raise RuntimeError("Training aborted: empty dataset")
        if not self._ensure_model_components():
            self.is_trained = False
            raise RuntimeError("Training aborted: ML dependencies unavailable")

        logger.info("Training dataset size: %s", len(texts))
        logger.info("Unique labels: %s", len(set(labels)))

        unique_labels = {str(label) for label in labels}
        if len(unique_labels) == 1:
            self.last_warning = "Entrenamiento insuficiente: solo una categoría detectada."
            return

        logger.info("Iniciando vectorización de entrenamiento")
        try:
            features = self.vectorizer.fit_transform(texts)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Training failed during vectorization")
            raise RuntimeError(f"Training failed during vectorization: {exc.__class__.__name__}: {exc}") from exc

        logger.info("Vectorized matrix shape: %s", features.shape)
        if features.shape[1] == 0:
            raise RuntimeError("Training aborted: vectorizer produced 0 features (empty vocabulary)")

        try:
            self.classifier.fit(features, labels)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Training failed during fit()")
            raise RuntimeError(f"Training failed during fit(): {str(exc)}") from exc

        self.is_trained = True
        self.last_warning = None

    def partial_fit(self, texts: Sequence[str], labels: Sequence[str], classes: Sequence[str] | None = None) -> None:
        if not texts or not self._ensure_model_components():
            return

        unique_labels = {str(label) for label in labels}
        if len(unique_labels) == 1:
            self.last_warning = "Entrenamiento insuficiente: solo una categoría detectada."
            return

        vectorizer_ready = hasattr(self.vectorizer, "vocabulary_") and bool(self.vectorizer.vocabulary_)
        features = self.vectorizer.transform(texts) if vectorizer_ready else self.vectorizer.fit_transform(texts)
        model_classes = list(classes or self.DEFAULT_CLASSES)
        if self.is_trained:
            self.classifier.partial_fit(features, labels)
        else:
            self.classifier.partial_fit(features, labels, classes=model_classes)
            self.is_trained = True
        self.last_warning = None

    def predict(self, text: str) -> str | None:
        if not self.is_available or not self.is_trained:
            return None
        if self.vectorizer is None or self.classifier is None:
            return None
        features = self.vectorizer.transform([text])
        return str(self.classifier.predict(features)[0])

    def predict_with_confidence(self, text: str) -> tuple[str | None, float]:
        if not self.is_available or not self.is_trained:
            return None, 0.0
        if self.vectorizer is None or self.classifier is None:
            return None, 0.0

        features = self.vectorizer.transform([text])
        prediction = str(self.classifier.predict(features)[0])

        confidence = 0.0
        if hasattr(self.classifier, "predict_proba"):
            probabilities = self.classifier.predict_proba(features)
            confidence = float(probabilities.max()) if probabilities.size else 0.0
        return prediction, confidence

    def save(self) -> None:
        if not self.is_available or self.classifier is None or self.vectorizer is None:
            return
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"classifier": self.classifier, "vectorizer": self.vectorizer, "is_trained": self.is_trained},
            self.model_path,
        )

    def load(self) -> bool:
        if not self.is_available or not self.model_path.exists():
            return False
        payload = joblib.load(self.model_path)
        self.classifier = payload["classifier"]
        self.vectorizer = payload.get("vectorizer")
        if self.vectorizer is None:
            self.vectorizer = TfidfVectorizer(ngram_range=(1, 2))
        self.is_trained = bool(payload.get("is_trained", True))
        return self.is_trained

    @staticmethod
    def compose_features(subject: str | None, sender: str | None, body_text: str | None, max_words: int = 80) -> str:
        body_tokens = (body_text or "").split()
        body_slice = " ".join(body_tokens[:max_words])
        return f"subject: {(subject or '').strip()}\nsender: {(sender or '').strip()}\nbody: {body_slice.strip()}"
