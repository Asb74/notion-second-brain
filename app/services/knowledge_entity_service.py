"""Offline entity extraction for Knowledge notes.

This module intentionally avoids AI and network dependencies.  The rules are
conservative heuristics meant to create a useful first navigation layer that can
be reviewed and improved over time.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS_PER_FIELD = 80_000
MAX_ENTITIES_PER_SOURCE = 250

ENTITY_TYPES = {"person", "organization", "email", "phone", "url", "date", "location", "other"}

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.IGNORECASE)
URL_RE = re.compile(
    r"\b((?:https?://|www\.)[^\s<>()\[\]{}\"']+|(?:[a-z0-9-]+\.)+(?:com|es|org|net|info|io|co|eu|dev|app)\b(?:/[^\s<>()\[\]{}\"']*)?)",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?34[\s.-]*)?(?:[6789]\d{2}(?:[\s.-]*\d{3}){2}|[6789]\d{8}|[6789]\d(?:[\s.-]*\d{2}){3})(?!\d)")
DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}(?:[T ][0-2]\d:[0-5]\d(?::[0-5]\d)?(?:\.\d+)?Z?)?|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"
)
ORG_SUFFIX_RE = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÜÑáéíóúüñ&.,' -]{1,80}?\s+(?:S\.?\s*L\.?|S\.?\s*A\.?|S\.?\s*C\.?\s*A\.?|Cooperativa|Sociedad(?:\s+Limitada|\s+Anónima)?))\b",
    re.IGNORECASE,
)
ORG_KEYWORD_RE = re.compile(
    r"\b(Mercadona|Anecoop|CaixaBank|Santander|BBVA|(?:Ayuntamiento|Banco|Universidad|Hermandad|Fundación|Asociación|Consejería|Ministerio|Diputación|Junta)(?:\s+(?:de|del|la|las|los|[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÜÑáéíóúüñ&.'-]+)){0,4})\b"
)
PERSON_RE = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúüñ]+(?:\s+(?:de|del|la|las|los|y|e|[A-ZÁÉÍÓÚÑ][a-záéíóúüñ]+)){1,5})\b")
LOCATION_RE = re.compile(
    r"\b(Sevilla|San Sebastián|Donostia|Madrid|Barcelona|Valencia|Málaga|Córdoba|Granada|Huelva|Cádiz|Almería|Jaén|Murcia|Alicante|Zaragoza|Bilbao|España|Andalucía)\b",
    re.IGNORECASE,
)

MONTHS_DAYS = {
    "lunes", "martes", "miercoles", "miércoles", "jueves", "viernes", "sabado", "sábado", "domingo",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
}
PERSON_STOPWORDS = MONTHS_DAYS | {
    "nota", "notas", "reunion", "reunión", "documento", "procedimiento", "archivo", "evernote", "email", "correo",
    "personal", "trabajo", "sansebas", "knowledge", "manager", "contenido", "resumen", "adjuntos", "factura", "pedido",
    "tel", "telefono", "teléfono", "fecha", "area", "área", "tema", "tipo", "fuente",
}
ORG_DOMAIN_STOPWORDS = {"gmail", "hotmail", "outlook", "yahoo", "icloud", "me", "live", "msn", "proton", "telefonica"}


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_entity_value(entity_type: str, value: str) -> str:
    """Normalize an entity value for deduplication."""
    cleaned = " ".join(str(value or "").strip().split()).strip(" ,.;:()[]{}<>")
    if entity_type == "email":
        return cleaned.lower()
    if entity_type == "url":
        url = cleaned.rstrip("/.,;)").lower()
        if url.startswith("www."):
            url = "http://" + url
        return url.rstrip("/")
    if entity_type == "phone":
        has_plus34 = cleaned.replace(" ", "").startswith("+34") or cleaned.replace(" ", "").startswith("0034")
        digits = re.sub(r"\D+", "", cleaned)
        if digits.startswith("0034"):
            digits = "34" + digits[4:]
        if has_plus34 and not digits.startswith("34"):
            digits = "34" + digits
        return digits
    if entity_type == "date":
        return _normalize_date(cleaned)
    return _strip_accents(cleaned).casefold()


def _normalize_date(value: str) -> str:
    cleaned = value.strip()
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", cleaned)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"
    slash_match = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", cleaned)
    if slash_match:
        day = int(slash_match.group(1))
        month = int(slash_match.group(2))
        year = int(slash_match.group(3))
        if year < 100:
            year += 2000 if year < 70 else 1900
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return _strip_accents(cleaned).casefold()


def _snippet(text: str, start: int, end: int, context: int = 70) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    prefix = "..." if left else ""
    suffix = "..." if right < len(text) else ""
    return prefix + " ".join(text[left:right].split()) + suffix


def _add_entity(results: list[dict[str, Any]], seen: set[tuple[str, str]], entity_type: str, value: str, source: str, snippet: str, confidence: float) -> None:
    cleaned = " ".join(str(value or "").strip().split()).strip(" ,.;:()[]{}<>")
    if not cleaned or entity_type not in ENTITY_TYPES:
        return
    normalized = normalize_entity_value(entity_type, cleaned)
    if not normalized:
        return
    key = (entity_type, normalized)
    if key in seen:
        return
    seen.add(key)
    results.append(
        {
            "type": entity_type,
            "value": cleaned,
            "normalized_value": normalized,
            "source": source,
            "snippet": snippet[:500],
            "confidence": float(confidence),
        }
    )



def _clean_organization_value(value: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip(" ,.;:()[]{}<>")
    suffix_match = re.search(r"(?i)(S\.?\s*L\.?|S\.?\s*A\.?|S\.?\s*C\.?\s*A\.?|Cooperativa|Sociedad(?:\s+Limitada|\s+Anónima)?)$", cleaned)
    if suffix_match:
        before = cleaned[: suffix_match.start()].strip()
        words = before.split()
        kept: list[str] = []
        for word in reversed(words):
            if re.match(r"^[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÜÑáéíóúüñ&.'-]*$", word):
                kept.insert(0, word)
                continue
            if kept and word.lower() in {"de", "del", "la", "las", "los", "y", "e"}:
                kept.insert(0, word)
                continue
            break
        if kept:
            cleaned = " ".join([*kept, suffix_match.group(1)])
    return cleaned

def _extract_email_domain_organizations(text: str, source: str, results: list[dict[str, Any]], seen: set[tuple[str, str]]) -> None:
    for match in EMAIL_RE.finditer(text):
        domain = match.group(1).split("@", 1)[-1].split(".", 1)[0]
        domain = domain.replace("-", " ").strip()
        normalized = _strip_accents(domain).casefold()
        if len(domain) < 4 or normalized in ORG_DOMAIN_STOPWORDS:
            continue
        value = domain.title()
        _add_entity(results, seen, "organization", value, source, _snippet(text, match.start(), match.end()), 0.35)


def _looks_like_false_phone(value: str) -> bool:
    digits = re.sub(r"\D+", "", value)
    if len(digits) not in {9, 11}:
        return True
    local = digits[-9:]
    if not re.match(r"^[6789]\d{8}$", local):
        return True
    return False


def _looks_like_person(value: str) -> bool:
    words = [word for word in value.split() if word.lower() not in {"de", "del", "la", "las", "los", "y", "e"}]
    if not 2 <= len(words) <= 4:
        return False
    normalized_words = {_strip_accents(word).casefold() for word in words}
    if normalized_words & {_strip_accents(word).casefold() for word in PERSON_STOPWORDS}:
        return False
    if any(len(word) <= 2 for word in words):
        return False
    return True


def extract_entities_from_text(text: str, source: str = "indexed_text") -> list[dict[str, Any]]:
    """Extract basic entities from a text block using local regex/heuristics only."""
    haystack = str(text or "")[:MAX_TEXT_CHARS_PER_FIELD]
    if not haystack.strip():
        return []

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for match in EMAIL_RE.finditer(haystack):
        _add_entity(results, seen, "email", match.group(1), source, _snippet(haystack, match.start(), match.end()), 0.95)

    for match in URL_RE.finditer(haystack):
        value = match.group(1).rstrip(".,;)")
        if "@" in value or (match.start() > 0 and haystack[match.start() - 1] == "@"):
            continue
        _add_entity(results, seen, "url", value, source, _snippet(haystack, match.start(), match.end()), 0.9)

    for match in PHONE_RE.finditer(haystack):
        value = match.group(0)
        if _looks_like_false_phone(value):
            continue
        _add_entity(results, seen, "phone", value, source, _snippet(haystack, match.start(), match.end()), 0.82)

    for match in DATE_RE.finditer(haystack):
        normalized = normalize_entity_value("date", match.group(1))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", normalized):
            _add_entity(results, seen, "date", match.group(1), source, _snippet(haystack, match.start(), match.end()), 0.8)

    for pattern, confidence in ((ORG_SUFFIX_RE, 0.78), (ORG_KEYWORD_RE, 0.72)):
        for match in pattern.finditer(haystack):
            value = _clean_organization_value(match.group(1))
            if len(value) > 90:
                continue
            _add_entity(results, seen, "organization", value, source, _snippet(haystack, match.start(), match.end()), confidence)

    _extract_email_domain_organizations(haystack, source, results, seen)

    for match in PERSON_RE.finditer(haystack):
        value = match.group(1)
        if _looks_like_person(value):
            _add_entity(results, seen, "person", value, source, _snippet(haystack, match.start(), match.end()), 0.48)

    for match in LOCATION_RE.finditer(haystack):
        _add_entity(results, seen, "location", match.group(1), source, _snippet(haystack, match.start(), match.end()), 0.55)

    return results[:MAX_ENTITIES_PER_SOURCE]


def _note_value(note: dict[str, Any] | sqlite3.Row | Any, key: str, default: Any = "") -> Any:
    try:
        if isinstance(note, sqlite3.Row) and key in note.keys():
            return note[key]
        if isinstance(note, dict):
            return note.get(key, default)
        return getattr(note, key, default)
    except Exception:  # noqa: BLE001
        return default


def extract_entities_for_note(note: dict[str, Any] | sqlite3.Row | Any) -> list[dict[str, Any]]:
    """Extract entities from title, content, tags and indexed_text of a Knowledge note."""
    fields: list[tuple[str, str]] = [
        ("title", str(_note_value(note, "title", "") or "")),
        ("content", str(_note_value(note, "content", "") or "")),
    ]
    tags = _note_value(note, "tags", []) or []
    if isinstance(tags, str):
        tags_text = tags
    else:
        tags_text = ", ".join(str(tag) for tag in tags if str(tag).strip())
    fields.append(("tags", tags_text))
    indexed_text = str(_note_value(note, "indexed_text", "") or "")
    if indexed_text.strip():
        fields.append(("indexed_text", indexed_text))

    entities: list[dict[str, Any]] = []
    seen_source: set[tuple[str, str, str]] = set()
    for source, text in fields:
        for entity in extract_entities_from_text(text, source=source):
            key = (str(entity["type"]), str(entity["normalized_value"]), source)
            if key in seen_source:
                continue
            seen_source.add(key)
            entities.append(entity)
    return entities


def rebuild_entities_for_note(note_id: int, conn: sqlite3.Connection) -> dict[str, int | bool]:
    """Rebuild entity links for one note using the repository attached to conn."""
    from app.persistence.knowledge_repository import KnowledgeRepository

    repo = KnowledgeRepository(conn)
    row = repo.get_item(int(note_id))
    if row is None:
        return {"ok": False, "entities": 0, "links": 0}
    note = dict(row)
    note["tags"] = repo.get_tags_for_item(int(note_id))
    entities = extract_entities_for_note(note)
    result = repo.replace_entities_for_item(int(note_id), entities)
    logger.info("KNOWLEDGE_ENTITY: extracted note_id=%s count=%s", note_id, len(entities))
    return {"ok": True, **result}


def rebuild_all_entities(conn: sqlite3.Connection) -> dict[str, int | float]:
    """Rebuild entities for every active Knowledge note."""
    rows = conn.execute("SELECT id FROM knowledge_items WHERE status != 'deleted' ORDER BY id ASC").fetchall()
    total = len(rows)
    logger.info("KNOWLEDGE_ENTITY: rebuild started total=%s", total)
    started = time.monotonic()
    notes = entities = links = errors = 0
    for row in rows:
        note_id = int(row["id"])
        try:
            result = rebuild_entities_for_note(note_id, conn)
            if result.get("ok"):
                notes += 1
                entities += int(result.get("entities") or 0)
                links += int(result.get("links") or 0)
            else:
                errors += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("KNOWLEDGE_ENTITY: error note_id=%s reason=%s", note_id, exc)
    seconds = time.monotonic() - started
    logger.info(
        "KNOWLEDGE_ENTITY: rebuild finished notes=%s entities=%s links=%s errors=%s",
        notes,
        entities,
        links,
        errors,
    )
    return {"total": total, "notes": notes, "entities": entities, "links": links, "errors": errors, "seconds": seconds}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
