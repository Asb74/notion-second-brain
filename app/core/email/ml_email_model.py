"""Incremental ML model for email classification."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

try:
    import joblib
    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.linear_model import SGDClassifier
except Exception:  # noqa: BLE001
    joblib = None
    HashingVectorizer = None
    SGDClassifier = None


class MLEmailModel:
    """Encapsulates a lightweight incremental text classifier."""

    CLASSES = ["priority", "order", "subscription", "marketing", "other"]

    def __init__(self, model_path: str | Path):
        self.model_path = Path(model_path)
        self.is_available = bool(joblib and HashingVectorizer and SGDClassifier)
        self.is_trained = False
        if self.is_available:
            self.vectorizer = HashingVectorizer(
                n_features=2**18,
                alternate_sign=False,
                norm="l2",
                ngram_range=(1, 2),
            )
            self.classifier = SGDClassifier(loss="log_loss", random_state=42)

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> None:
        if not self.is_available or not texts:
            self.is_trained = False
            return
        features = self.vectorizer.transform(texts)
        self.classifier.partial_fit(features, labels, classes=self.CLASSES)
        self.is_trained = True

    def partial_fit(self, texts: Sequence[str], labels: Sequence[str]) -> None:
        if not self.is_available or not texts:
            return
        features = self.vectorizer.transform(texts)
        if self.is_trained:
            self.classifier.partial_fit(features, labels)
        else:
            self.classifier.partial_fit(features, labels, classes=self.CLASSES)
            self.is_trained = True

    def predict(self, text: str) -> str | None:
        if not self.is_available or not self.is_trained:
            return None
        features = self.vectorizer.transform([text])
        return str(self.classifier.predict(features)[0])

    def save(self) -> None:
        if not self.is_available:
            return
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"classifier": self.classifier, "is_trained": self.is_trained}, self.model_path)

    def load(self) -> bool:
        if not self.is_available or not self.model_path.exists():
            return False
        payload = joblib.load(self.model_path)
        self.classifier = payload["classifier"]
        self.is_trained = bool(payload.get("is_trained", True))
        return self.is_trained

    @staticmethod
    def compose_features(subject: str | None, sender: str | None, body_text: str | None, max_words: int = 80) -> str:
        body_tokens = (body_text or "").split()
        body_slice = " ".join(body_tokens[:max_words])
        return f"subject: {(subject or '').strip()}\nsender: {(sender or '').strip()}\nbody: {body_slice.strip()}"
