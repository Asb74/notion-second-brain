"""Rule-based metadata suggestions for Knowledge Manager notes.

This module is intentionally UI- and source-agnostic so it can be reused by
email conversions today and by future importers (Evernote, PDF, DOCX, etc.).
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

MAX_SUGGESTED_TAGS = 6


def _fold(text: str) -> str:
    """Return a lowercase, accent-insensitive representation for matching."""
    normalized = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.casefold()


def _has_any(haystack: str, keywords: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(_fold(keyword))}\b", haystack) for keyword in keywords)


def _title_tag(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        return ""
    known_acronyms = {"ip": "IP", "pdf": "PDF", "docx": "DOCX"}
    folded = _fold(cleaned)
    if folded in known_acronyms:
        return known_acronyms[folded]
    return " ".join(part[:1].upper() + part[1:].lower() for part in cleaned.split(" "))


def _normalize_tags(tags: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        normalized = _title_tag(tag)
        key = _fold(normalized)
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
        if len(result) >= MAX_SUGGESTED_TAGS:
            break
    return result


def suggest_knowledge_metadata(
    title: str,
    content: str,
    source: str = "",
    existing_area: str | None = None,
    existing_topic: str | None = None,
    existing_type: str | None = None,
    existing_tags: list[str] | None = None,
) -> dict[str, object]:
    """Suggest area, topic, type and tags using deterministic keyword rules.

    The returned area/type values are conservative and aligned with the default
    global masters where possible. Callers should still validate them against
    the local masters before persisting.
    """
    combined = _fold("\n".join([title or "", content or "", source or ""]))
    source_key = _fold(source or "")

    suggested_area = (existing_area or "").strip()
    suggested_topic = (existing_topic or "").strip()
    suggested_type = (existing_type or "").strip()
    tags: list[str] = list(existing_tags or [])
    reasons: list[str] = []

    if source_key:
        tags.append(source_key)
        reasons.append(f"fuente={source_key}")

    rules: tuple[dict[str, object], ...] = (
        {
            "keywords": ("factura", "albaran", "proveedor", "importe", "vencimiento"),
            "tags": ("Factura", "Proveedor", "Administración"),
            "area": "Trabajo",
            "type": "Documento",
            "reason": "documentación administrativa",
        },
        {
            "keywords": ("pedido", "entrega", "cliente", "mercancia"),
            "tags": ("Pedido", "Cliente"),
            "area": "Trabajo",
            "type": "Documento",
            "reason": "pedido o entrega",
        },
        {
            "keywords": ("reunion", "acta", "acuerdo", "seguimiento"),
            "tags": ("Reunión", "Acta"),
            "area": "Trabajo",
            "type": "Reunión",
            "reason": "reunión/seguimiento",
        },
        {
            "keywords": ("contrato", "arrendamiento", "clausula"),
            "tags": ("Contrato", "Legal"),
            "area": "Trabajo",
            "type": "Documento",
            "reason": "contrato/legal",
        },
        {
            "keywords": ("camara", "ip", "red", "router", "conexion"),
            "tags": ("Tecnología", "Cámaras"),
            "area": "Informática",
            "type": "Nota",
            "reason": "tecnología/redes",
        },
        {
            "keywords": ("abono", "renovacion", "club"),
            "tags": ("Suscripción", "Renovación"),
            "area": "Personal",
            "type": "Nota",
            "reason": "suscripción/renovación",
        },
        {
            "keywords": ("cultivo", "finca", "riego", "tratamiento"),
            "tags": ("Agrícola", "Campo"),
            "area": "Trabajo",
            "type": "Nota",
            "reason": "actividad agrícola",
        },
    )

    for rule in rules:
        keywords = tuple(str(keyword) for keyword in rule["keywords"])
        if not _has_any(combined, keywords):
            continue
        tags.extend(str(tag) for tag in rule["tags"])
        if not suggested_area:
            suggested_area = str(rule["area"])
        if not suggested_type:
            suggested_type = str(rule["type"])
        reasons.append(str(rule["reason"]))

    if not suggested_area:
        suggested_area = "Archivo"
    if not suggested_type:
        suggested_type = "Nota"

    normalized_tags = _normalize_tags(tags)
    reason = "; ".join(dict.fromkeys(reason for reason in reasons if reason))
    logger.info(
        "KNOWLEDGE_SUGGEST: sugerencias generadas tags=%s area=%s type=%s",
        len(normalized_tags),
        suggested_area,
        suggested_type,
    )
    return {
        "area": suggested_area,
        "topic": suggested_topic,
        "type": suggested_type,
        "tags": normalized_tags,
        "reason": reason,
    }
