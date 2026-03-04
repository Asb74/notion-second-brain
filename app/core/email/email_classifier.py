"""Simple rule-based email classifier."""

from __future__ import annotations


class EmailClassifier:
    """Classify emails into executive action-oriented types."""

    def classify(self, subject: str | None, sender: str | None, body_text: str | None) -> str:
        """Return one of: priority, order, subscription, marketing, info, other."""
        normalized_subject = (subject or "").lower()
        _ = sender  # reserved for future rules

        if "pedido" in normalized_subject or "order" in normalized_subject:
            return "order"
        if "newsletter" in normalized_subject or "resumen semanal" in normalized_subject:
            return "subscription"
        if "oferta" in normalized_subject or "descuento" in normalized_subject:
            return "marketing"

        _ = body_text  # reserved for future rules
        return "priority"

