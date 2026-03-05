"""Email entity extraction utilities."""

from __future__ import annotations

import re


class EmailEntityExtractor:
    """Extract key entities from email subject and body text."""

    ORDER_RE = re.compile(r"pedido\s*(\d{5,7})", re.IGNORECASE)
    EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+", re.IGNORECASE)
    ACTION_WORDS = ("contactar", "revisar", "confirmar", "enviar", "gestionar")

    CLIENT_RE = re.compile(r"cliente\s*[:\-]\s*([^\n\r;]+)", re.IGNORECASE)
    PRODUCT_RE = re.compile(r"producto\s*[:\-]\s*([^\n\r;]+)", re.IGNORECASE)
    PERSON_RE = re.compile(r"(?:persona|contacto|atenci[oó]n)\s*[:\-]\s*([^\n\r;]+)", re.IGNORECASE)

    @classmethod
    def extract_entities(cls, subject: str, body: str) -> dict[str, str]:
        text = f"{subject or ''}\n{body or ''}".strip()
        entities = {
            "pedido": "",
            "cliente": "",
            "producto": "",
            "persona": "",
            "email_persona": "",
            "accion": "",
        }

        order_match = cls.ORDER_RE.search(text)
        if order_match:
            entities["pedido"] = order_match.group(1)

        email_match = cls.EMAIL_RE.search(text)
        if email_match:
            entities["email_persona"] = email_match.group(0)

        entities["cliente"] = cls._extract_by_pattern(cls.CLIENT_RE, text)
        entities["producto"] = cls._extract_by_pattern(cls.PRODUCT_RE, text)
        entities["persona"] = cls._extract_by_pattern(cls.PERSON_RE, text)
        if not entities["persona"] and entities["email_persona"]:
            entities["persona"] = cls._person_from_email(entities["email_persona"])

        lowered = text.lower()
        for action in cls.ACTION_WORDS:
            if action in lowered:
                entities["accion"] = action
                break

        return entities

    @staticmethod
    def _extract_by_pattern(pattern: re.Pattern[str], text: str) -> str:
        match = pattern.search(text)
        if not match:
            return ""
        return (match.group(1) or "").strip().strip(" ,.")

    @staticmethod
    def _person_from_email(email_value: str) -> str:
        local_part = (email_value.split("@", 1)[0] if "@" in email_value else "").strip()
        if not local_part:
            return ""
        readable = re.sub(r"[._-]+", " ", local_part)
        return readable.title().strip()
