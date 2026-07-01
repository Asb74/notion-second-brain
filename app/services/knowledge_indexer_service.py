"""Safe local text indexing helpers for Knowledge notes and attachments."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import mimetypes
import re
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_CHARS = 100_000
MAX_INDEXED_TEXT_CHARS = 300_000
MAX_PDF_PAGES = 250
MAX_XLSX_SHEETS = 10
MAX_XLSX_ROWS = 2_000
MAX_XLSX_COLUMNS = 100

_OCR_SIGNS_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_ocr_text_for_search(text: str) -> str:
    """Normalize OCR text for tolerant search without replacing the original OCR output."""
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).casefold()
    normalized = _OCR_SIGNS_RE.sub(" ", normalized)
    tokens: list[str] = []
    for token in normalized.split():
        if any(char.isalpha() for char in token):
            token = re.sub(r"(?<=[a-zñ])1(?=[a-zñ])", "i", token)
            token = re.sub(r"(?<=[a-zñ])0(?=[a-zñ])", "o", token)
            if "rn" in token and "carni" not in token:
                tokens.append(token.replace("rn", "m"))
            if token.endswith("1a"):
                token = f"{token[:-2]}ia"
            if token.endswith("la") and len(token) > 5:
                tokens.append(f"{token[:-2]}ia")
        tokens.append(token)
    return re.sub(r"\s+", " ", " ".join(tokens)).strip()

_TEXT_EXTENSIONS = {".txt", ".csv", ".log", ".md", ".json", ".xml", ".html", ".htm"}
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _value(mapping: Any, key: str, default: Any = "") -> Any:
    if mapping is None:
        return default
    if isinstance(mapping, dict):
        return mapping.get(key, default)
    try:
        return mapping[key]
    except Exception:  # noqa: BLE001
        return default


def _trim(text: str, limit: int, *, context: str) -> str:
    if len(text) <= limit:
        return text
    logger.warning("KNOWLEDGE_INDEX: %s trimmed chars=%s limit=%s", context, len(text), limit)
    return text[:limit]


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding, errors="replace")
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=%s", path.name, exc)
            return ""
    return ""


def _extract_pdf_text(path: Path) -> str:
    if not _module_available("fitz"):
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=PyMuPDF no disponible", path.name)
        return ""
    fitz = importlib.import_module("fitz")
    try:
        chunks: list[str] = []
        with fitz.open(path) as document:
            page_count = len(document)
            if page_count > MAX_PDF_PAGES:
                logger.warning(
                    "KNOWLEDGE_INDEX: attachment pdf trimmed filename=%s pages=%s limit=%s",
                    path.name,
                    page_count,
                    MAX_PDF_PAGES,
                )
            for page_index in range(min(page_count, MAX_PDF_PAGES)):
                page = document.load_page(page_index)
                chunks.append(page.get_text("text"))
                if sum(len(chunk) for chunk in chunks) >= MAX_ATTACHMENT_CHARS:
                    break
        return "\n".join(chunks)
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=%s", path.name, exc)
        return ""


def _extract_docx_text(path: Path) -> str:
    if not _module_available("docx"):
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=python-docx no disponible", path.name)
        return ""
    docx = importlib.import_module("docx")
    try:
        document = docx.Document(path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text)
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=%s", path.name, exc)
        return ""


def _extract_xlsx_text(path: Path) -> str:
    if not _module_available("openpyxl"):
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=openpyxl no disponible", path.name)
        return ""
    openpyxl = importlib.import_module("openpyxl")
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=%s", path.name, exc)
        return ""

    chunks: list[str] = []
    try:
        sheet_names = workbook.sheetnames[:MAX_XLSX_SHEETS]
        if len(workbook.sheetnames) > MAX_XLSX_SHEETS:
            logger.warning(
                "KNOWLEDGE_INDEX: attachment xlsx sheets trimmed filename=%s sheets=%s limit=%s",
                path.name,
                len(workbook.sheetnames),
                MAX_XLSX_SHEETS,
            )
        for sheet_name in sheet_names:
            sheet = workbook[sheet_name]
            chunks.append(f"Hoja: {sheet_name}")
            for row in sheet.iter_rows(max_row=MAX_XLSX_ROWS, max_col=MAX_XLSX_COLUMNS, values_only=True):
                values = [str(value) for value in row if value not in (None, "")]
                if values:
                    chunks.append("\t".join(values))
                if sum(len(chunk) for chunk in chunks) >= MAX_ATTACHMENT_CHARS:
                    return "\n".join(chunks)
        return "\n".join(chunks)
    finally:
        try:
            workbook.close()
        except Exception:  # noqa: BLE001
            pass


def extract_text_from_attachment(path: str | Path, mime: str = "", filename: str = "") -> str:
    """Extract searchable text from one attachment without raising UI-breaking errors."""
    attachment_path = Path(path)
    display_name = filename or attachment_path.name
    if not attachment_path.exists() or not attachment_path.is_file():
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=missing_file", display_name)
        return ""

    guessed_mime = mime or mimetypes.guess_type(str(attachment_path))[0] or ""
    normalized_mime = guessed_mime.lower().strip()
    suffix = attachment_path.suffix.lower()

    if normalized_mime == "application/pdf" or suffix == ".pdf":
        text = _extract_pdf_text(attachment_path)
    elif normalized_mime == _DOCX_MIME or suffix == ".docx":
        text = _extract_docx_text(attachment_path)
    elif normalized_mime == _XLSX_MIME or suffix in {".xlsx", ".xlsm", ".xltx"}:
        text = _extract_xlsx_text(attachment_path)
    elif normalized_mime.startswith("text/") or suffix in _TEXT_EXTENSIONS:
        text = _read_text_file(attachment_path)
    else:
        logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=unsupported_mime mime=%s", display_name, guessed_mime)
        text = ""

    text = _trim(text, MAX_ATTACHMENT_CHARS, context=f"attachment filename={display_name}").strip()
    logger.info("KNOWLEDGE_INDEX: attachment indexed filename=%s chars=%s", display_name, len(text))
    return text


def build_indexed_text(note: dict[str, Any] | Any, attachments: list[dict[str, Any] | Any]) -> str:
    """Build the full indexed text payload for a Knowledge note."""
    tags = _value(note, "tags", []) or []
    if isinstance(tags, str):
        tags_text = tags
    else:
        tags_text = ", ".join(str(tag) for tag in tags if str(tag).strip())

    parts = [
        str(_value(note, "title", "") or ""),
        str(_value(note, "area", "") or _value(note, "area_name", "") or ""),
        str(_value(note, "topic", "") or _value(note, "topic_name", "") or ""),
        str(_value(note, "tipo", "") or _value(note, "item_type_name", "") or ""),
        tags_text,
        str(_value(note, "source_type", "") or _value(note, "source", "") or ""),
        str(_value(note, "source_id", "") or ""),
        str(_value(note, "source_path", "") or ""),
        str(_value(note, "content", "") or ""),
        str(_value(note, "summary", "") or ""),
    ]

    for attachment in attachments:
        filename = str(
            _value(attachment, "original_filename", "")
            or _value(attachment, "stored_filename", "")
            or _value(attachment, "filename", "")
            or ""
        )
        mime = str(_value(attachment, "mime_type", "") or _value(attachment, "mime", "") or "")
        path = str(_value(attachment, "stored_path", "") or _value(attachment, "path", "") or "")
        parts.extend([filename, mime])
        if path:
            parts.append(extract_text_from_attachment(path, mime, filename))
        elif filename:
            logger.info("KNOWLEDGE_INDEX: attachment skipped filename=%s reason=missing_path", filename)
        corrected_ocr_text = str(_value(attachment, "ocr_text_corrected", "") or "").strip()
        raw_ocr_text = str(_value(attachment, "ocr_text_raw", "") or _value(attachment, "ocr_text", "") or "").strip()
        ocr_text = corrected_ocr_text or raw_ocr_text
        if ocr_text:
            normalized_ocr_text = normalize_ocr_text_for_search(ocr_text)
            marker = "OCR corregido" if corrected_ocr_text else "OCR"
            parts.append(f"[{marker}: {filename or 'adjunto'}]\n{ocr_text}")
            if normalized_ocr_text:
                parts.append(f"[{marker}_NORMALIZADO: {filename or 'adjunto'}]\n{normalized_ocr_text}")

    indexed_text = "\n".join(part for part in parts if part).strip()
    return _trim(indexed_text, MAX_INDEXED_TEXT_CHARS, context=f"note_id={_value(note, 'id', '')}")


def index_note(note: dict[str, Any] | Any, attachments: list[dict[str, Any] | Any]) -> dict[str, Any]:
    """Return an index payload for repository persistence."""
    indexed_text = build_indexed_text(note, attachments)
    return {"indexed_text": indexed_text, "chars": len(indexed_text)}
