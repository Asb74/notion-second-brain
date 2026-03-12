"""Reusable attachment text extraction helpers."""

from __future__ import annotations

import logging
from pathlib import Path

MAX_ATTACHMENT_TEXT = 20_000
MAX_CSV_CHARS = 12_000
MAX_SPREADSHEET_ROWS = 2_000
SUPPORTED_ATTACHMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".csv", ".xlsx", ".xls"}

logger = logging.getLogger(__name__)


def extract_text_from_attachment(file_path: str) -> str:
    """Extract plain text from a supported attachment path."""
    path = Path(str(file_path or "").strip())
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS or not path.exists():
        return ""

    try:
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix == ".docx":
            return _extract_docx(path)
        if suffix == ".doc":
            return _extract_doc(path)
        if suffix == ".txt":
            return _extract_txt(path)
        if suffix == ".csv":
            return _extract_csv(path)
        if suffix in {".xlsx", ".xls"}:
            return _extract_spreadsheet(path)
    except Exception:  # noqa: BLE001
        logger.exception("Attachment extraction failed: %s", path.name)
        return ""

    return ""


def extract_text_from_attachments(attachments: list[dict[str, str]]) -> str:
    """Extract and combine text from multiple supported attachments."""
    blocks: list[str] = []
    for attachment in attachments or []:
        local_path = str(attachment.get("file_path") or attachment.get("local_path") or "").strip()
        filename = str(attachment.get("filename") or Path(local_path).name or "adjunto").strip() or "adjunto"
        if not local_path:
            continue

        suffix = Path(filename).suffix.lower() or Path(local_path).suffix.lower()
        ext = suffix.lstrip(".")
        if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS:
            continue

        logger.info("Extracting text from attachment: %s", filename)
        logger.info("Attachment type supported: %s", ext)
        extracted = extract_text_from_attachment(local_path).strip()
        logger.info("Attachment text extracted: %s characters", len(extracted))
        if not extracted:
            continue

        blocks.append(
            f"ATTACHMENT: {filename}\n"
            "--------------------------------\n"
            f"{extracted}"
        )

    combined = "\n\n".join(blocks).strip()
    if len(combined) > MAX_ATTACHMENT_TEXT:
        return combined[:MAX_ATTACHMENT_TEXT]
    return combined


def _extract_pdf(path: Path) -> str:
    texts: list[str] = []

    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(path)) as doc:
            for page in doc.pages:
                text = str(page.extract_text() or "").strip()
                if text:
                    texts.append(text)
    except Exception:  # noqa: BLE001
        logger.warning("pdfplumber extraction failed for %s; trying PyPDF2 fallback", path.name)

    if texts:
        return "\n\n".join(texts)

    try:
        from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        for page in reader.pages:
            text = str(page.extract_text() or "").strip()
            if text:
                texts.append(text)
    except Exception:  # noqa: BLE001
        logger.warning("PyPDF2 extraction failed for %s; trying PyMuPDF fallback", path.name)

    if texts:
        return "\n\n".join(texts)

    try:
        import fitz  # type: ignore

        with fitz.open(path) as doc:
            for page in doc:
                text = (page.get_text("text") or "").strip()
                if text:
                    texts.append(text)
    except Exception:  # noqa: BLE001
        logger.warning("PyMuPDF extraction failed for %s; trying pdfminer fallback", path.name)

    if texts:
        return "\n\n".join(texts)

    try:
        from pdfminer.high_level import extract_text  # type: ignore

        return str(extract_text(str(path)) or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("pdfminer extraction failed for %s", path.name)
        return ""




def _extract_doc(path: Path) -> str:
    logger.warning("DOC extraction not supported for %s; skipping", path.name)
    return ""

def _extract_docx(path: Path) -> str:
    try:
        from docx import Document  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("python-docx is not available; cannot read %s", path.name)
        return ""

    document = Document(str(path))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text and paragraph.text.strip()]
    return "\n".join(paragraphs)


def _extract_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _extract_csv(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="ignore")
    if len(content) > MAX_CSV_CHARS:
        return content[:MAX_CSV_CHARS]
    return content


def _extract_spreadsheet(path: Path) -> str:
    try:
        import pandas as pd  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("pandas is not available; cannot read %s", path.name)
        return ""

    sheet_name = None if path.suffix.lower() == ".xlsx" else 0
    try:
        workbook = pd.read_excel(str(path), sheet_name=sheet_name, dtype=str)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse spreadsheet %s", path.name)
        return ""

    if not isinstance(workbook, dict):
        workbook = {"Sheet1": workbook}

    blocks: list[str] = []
    for sheet, frame in workbook.items():
        frame = frame.fillna("").astype(str)
        trimmed = frame.head(MAX_SPREADSHEET_ROWS)
        csv_text = trimmed.to_csv(index=False)
        blocks.append(f"[{sheet}]\n{csv_text.strip()}")

    return "\n\n".join(blocks).strip()
