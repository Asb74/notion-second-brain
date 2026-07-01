"""Optional OCR helpers for Knowledge attachments."""

from __future__ import annotations

import importlib
import logging
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import pytesseract
except Exception:  # noqa: BLE001
    pytesseract = None

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
PDF_EXTENSIONS = {".pdf"}
MAX_OCR_TEXT_CHARS = 120_000
MAX_OCR_PDF_PAGES = 5
PDF_OCR_TEXT_THRESHOLD = 80


def _configure_tesseract() -> None:
    from app.services.ocr_runtime import configure_pytesseract

    configure_pytesseract()


def _clean_text(text: str) -> str:
    lines = [" ".join(line.strip().split()) for line in str(text or "").splitlines()]
    cleaned = "\n".join(line for line in lines if line).strip()
    if len(cleaned) > MAX_OCR_TEXT_CHARS:
        logger.warning("KNOWLEDGE_OCR: text trimmed chars=%s limit=%s", len(cleaned), MAX_OCR_TEXT_CHARS)
        return cleaned[:MAX_OCR_TEXT_CHARS]
    return cleaned


def is_ocr_available() -> tuple[bool, str]:
    if pytesseract is None:
        reason = "OCR no disponible. Instala Tesseract OCR y pytesseract."
        logger.info("KNOWLEDGE_OCR: available=False reason=pytesseract_missing")
        return False, reason
    _configure_tesseract()
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: available=False reason=%s", exc)
        return False, "OCR no disponible. Instala Tesseract OCR y pytesseract."
    logger.info("KNOWLEDGE_OCR: available=True reason=ok")
    return True, "OCR disponible"


def is_image_candidate(path: str | Path, mime: str = "") -> bool:
    suffix = Path(path).suffix.lower()
    normalized_mime = (mime or mimetypes.guess_type(str(path))[0] or "").lower()
    return suffix in IMAGE_EXTENSIONS or normalized_mime.startswith("image/")


def is_pdf_candidate(path: str | Path, mime: str = "") -> bool:
    suffix = Path(path).suffix.lower()
    normalized_mime = (mime or mimetypes.guess_type(str(path))[0] or "").lower()
    return suffix in PDF_EXTENSIONS or normalized_mime == "application/pdf"


def ocr_image(path: str, lang: str = "spa+eng") -> str:
    available, reason = is_ocr_available()
    if not available:
        logger.info("KNOWLEDGE_OCR: skipped reason=%s", reason)
        return ""
    try:
        logger.info("KNOWLEDGE_OCR: image started path=%s", path)
        image_mod = importlib.import_module("PIL.Image")
        with image_mod.open(path) as image:
            text = pytesseract.image_to_string(image, lang=lang)
        cleaned = _clean_text(text)
        logger.info("KNOWLEDGE_OCR: image finished chars=%s", len(cleaned))
        return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: error reason=%s", exc)
        return ""


def _extract_pdf_probe_text(path: Path, pages: int) -> str:
    try:
        fitz = importlib.import_module("fitz")
        chunks: list[str] = []
        with fitz.open(path) as document:
            for page_index in range(min(len(document), pages)):
                chunks.append(document.load_page(page_index).get_text("text"))
        return _clean_text("\n".join(chunks))
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: pdf probe failed path=%s reason=%s", path, exc)
        return ""


def should_ocr_attachment(path: str | Path, mime: str = "", existing_text: str = "") -> bool:
    if is_image_candidate(path, mime):
        return True
    if not is_pdf_candidate(path, mime):
        logger.info("KNOWLEDGE_OCR: skipped reason=unsupported_file path=%s", path)
        return False
    probe = (existing_text or _extract_pdf_probe_text(Path(path), MAX_OCR_PDF_PAGES)).strip()
    if len(probe) >= PDF_OCR_TEXT_THRESHOLD:
        logger.info("KNOWLEDGE_OCR: skipped reason=pdf_has_text path=%s chars=%s", path, len(probe))
        return False
    return True


def ocr_pdf(path: str, max_pages: int = MAX_OCR_PDF_PAGES, lang: str = "spa+eng") -> str:
    available, reason = is_ocr_available()
    if not available:
        logger.info("KNOWLEDGE_OCR: skipped reason=%s", reason)
        return ""
    try:
        fitz = importlib.import_module("fitz")
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: error reason=PyMuPDF unavailable %s", exc)
        return ""
    chunks: list[str] = []
    try:
        logger.info("KNOWLEDGE_OCR: pdf started path=%s pages=%s", path, max_pages)
        with fitz.open(path) as document:
            for page_index in range(min(len(document), max_pages)):
                pix = document.load_page(page_index).get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = importlib.import_module("PIL.Image").frombytes("RGB", [pix.width, pix.height], pix.samples)
                page_text = _clean_text(pytesseract.image_to_string(image, lang=lang))
                logger.info("KNOWLEDGE_OCR: pdf page=%s chars=%s", page_index + 1, len(page_text))
                if page_text:
                    chunks.append(page_text)
                if sum(len(chunk) for chunk in chunks) >= MAX_OCR_TEXT_CHARS:
                    break
        return _clean_text("\n".join(chunks))
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: error reason=%s", exc)
        return ""
