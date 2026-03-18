import sqlite3

from app.core.email.email_classifier import EmailClassifier
from app.persistence.email_repository import EmailRepository


class _RepoSingleLabel:
    def find_forced_label_for_sender(self, _sender: str):
        return None

    def get_category_names(self):
        return ["priority", "order", "other"]

    def get_labeled_dataset(self):
        return [
            {"subject": "s", "sender": "a@x.com", "body_text": "b", "label": "priority"}
            for _ in range(40)
        ]


def test_classify_keeps_previous_type_with_low_ml_confidence() -> None:
    classifier = EmailClassifier(email_repo=None)
    classifier._classify_by_rules = lambda _subject, _sender: None  # type: ignore[method-assign]
    classifier.ml_model.predict_with_confidence = lambda _text: ("marketing", 0.2)  # type: ignore[method-assign]

    result = classifier.classify("Asunto neutro", "cliente@externo.com", "texto", previous_type="order")

    assert result == "order"


def test_retrain_protects_against_single_label_collapse() -> None:
    classifier = EmailClassifier(email_repo=_RepoSingleLabel())

    trained = classifier.retrain_if_possible(force=True)

    assert trained is False
    assert classifier.last_training_warning == "Entrenamiento cancelado: solo una categoría activa."


def test_reclassify_all_emails_excludes_user_labels() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = EmailRepository(conn)

    conn.executemany(
        """
        INSERT INTO emails (gmail_id, subject, sender, received_at, body_text, status, category, type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("g1", "S1", "u@x.com", "2024-01-01T00:00:00+00:00", "Body", "new", "pending", "other"),
            ("g2", "S2", "u@x.com", "2024-01-01T00:00:00+00:00", "Body", "new", "pending", "other"),
        ],
    )
    repo.save_label("g1", "order", source="user")
    repo.save_label("g2", "order", source="model")

    classifier = EmailClassifier(email_repo=repo)
    classifier.classify = lambda subject, sender, body_text, previous_type=None: "priority"  # type: ignore[method-assign]

    updated = classifier.reclassify_all_emails()

    g1_type = conn.execute("SELECT type FROM emails WHERE gmail_id = 'g1'").fetchone()["type"]
    g2_type = conn.execute("SELECT type FROM emails WHERE gmail_id = 'g2'").fetchone()["type"]

    assert updated == 1
    assert g1_type == "other"
    assert g2_type == "priority"


class _RepoMixedLabels:
    def find_forced_label_for_sender(self, _sender: str):
        return None

    def get_category_names(self):
        return ["priority", "order", "other"]

    def get_labeled_dataset(self):
        return [
            {"subject": f"s{i}", "sender": "a@x.com", "body_text": "texto", "label": "priority" if i % 2 == 0 else "order"}
            for i in range(20)
        ]


def test_retrain_propagates_detailed_fit_failure(monkeypatch) -> None:
    classifier = EmailClassifier(email_repo=None)
    classifier.email_repo = _RepoMixedLabels()

    def _raise_fit(self, _texts, _labels, classes=None):
        raise RuntimeError("Training aborted: vectorizer produced 0 features (empty vocabulary)")

    monkeypatch.setattr("app.core.email.email_classifier.MLEmailModel.fit", _raise_fit)

    trained = classifier.retrain_if_possible(force=True)

    assert trained is False
    assert classifier.last_training_warning == "Entrenamiento fallido: Training aborted: vectorizer produced 0 features (empty vocabulary)"


def test_retrain_propagates_fit_exception_details(monkeypatch) -> None:
    classifier = EmailClassifier(email_repo=None)
    classifier.email_repo = _RepoMixedLabels()

    def _raise_fit(self, _texts, _labels, classes=None):
        raise RuntimeError("Training failed during fit(): ValueError: inconsistent labels")

    monkeypatch.setattr("app.core.email.email_classifier.MLEmailModel.fit", _raise_fit)

    trained = classifier.retrain_if_possible(force=True)

    assert trained is False
    assert classifier.last_training_warning == "Entrenamiento fallido durante fit(): ValueError: inconsistent labels"


def test_incremental_training_uses_fixed_classes_in_partial_fit() -> None:
    classifier = EmailClassifier(email_repo=None)
    classifier.ml_model.is_trained = True
    classifier.ml_model.last_warning = None

    class _Vectorizer:
        def transform(self, _texts):
            return [[1.0]]

    class _InnerClassifier:
        def __init__(self):
            self.received_classes = None

        def partial_fit(self, _features, _labels, classes=None):
            self.received_classes = classes

    classifier.ml_model.vectorizer = _Vectorizer()
    classifier.ml_model.classifier = _InnerClassifier()
    classifier.ml_model.save = lambda: None

    trained = classifier.incremental_train_on_examples(["subject: demo"], ["order"])

    assert trained is True
    assert classifier.ml_model.classifier.received_classes == classifier.all_classes
