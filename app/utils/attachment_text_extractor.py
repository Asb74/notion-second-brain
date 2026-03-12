"""Reusable attachment text extraction helpers."""

from __future__ import annotations

import logging
from pathlib import Path

MAX_ATTACHMENT_TEXT = 20_000
MAX_CSV_CHARS = 12_000
MAX_SPREADSHEET_ROWS = 2_000
SUPPORTED_ATTACHMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv", ".xlsx", ".xls"}

logger = logging.getLogger(__name__)


def extract_text_from_attachment(file_path: str, filename: str = "") -> str:
    """Extract plain text from a supported attachment path."""
    path = Path(str(file_path or "").strip())
    suffix = _detect_extension(filename=filename, file_path=str(path))
    if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS or not path.exists():
        return ""

    try:
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix == ".docx":
            return _extract_docx(path)
        if suffix == ".txt":
            return _extract_txt(path)
        if suffix == ".csv":
            return _extract_csv(path)
        if suffix == ".xlsx":
            return _extract_xlsx(path)
        if suffix == ".xls":
            return _extract_xls(path)
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

        suffix = _detect_extension(filename=filename, file_path=local_path)
        if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS:
            continue

        logger.info("Extracting text from attachment: %s", filename)
        logger.info("Attachment type supported: %s", suffix.lstrip("."))
        extracted = extract_text_from_attachment(local_path, filename=filename).strip()
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


def _detect_extension(filename: str, file_path: str) -> str:
    candidates = [str(filename or "").strip().lower(), Path(file_path or "").name.lower()]
    for candidate in candidates:
        if candidate.endswith(".pdf"):
            return ".pdf"
        if candidate.endswith(".xlsx"):
            return ".xlsx"
        if candidate.endswith(".xls"):
            return ".xls"
        if candidate.endswith(".docx"):
            return ".docx"
        if candidate.endswith(".txt"):
            return ".txt"
        if candidate.endswith(".csv"):
            return ".csv"
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
    return _read_text_with_fallback(path).strip()


def _extract_csv(path: Path) -> str:
    content = _read_text_with_fallback(path)
    if len(content) > MAX_CSV_CHARS:
        return content[:MAX_CSV_CHARS]
    return content


def _extract_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("openpyxl is not available; cannot read %s", path.name)
        return ""

    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    blocks: list[str] = []
    rows_read = 0

    for sheet in workbook.worksheets:
        sheet_lines: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            if rows_read >= MAX_SPREADSHEET_ROWS:
                break
            values = [str(cell).strip() if cell is not None else "" for cell in row]
            line = " | ".join(value for value in values if value)
            if line:
                sheet_lines.append(line)
            rows_read += 1
        if sheet_lines:
            blocks.append(f"[{sheet.title}]\n" + "\n".join(sheet_lines))
        if rows_read >= MAX_SPREADSHEET_ROWS:
            break

    workbook.close()
    return "\n\n".join(blocks).strip()


def _extract_xls(path: Path) -> str:
    try:
        import xlrd  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("xlrd is not available; cannot read %s", path.name)
        return ""

    try:
        workbook = xlrd.open_workbook(str(path))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse spreadsheet %s", path.name)
        return ""

    blocks: list[str] = []
    rows_read = 0
    for sheet in workbook.sheets():
        sheet_lines: list[str] = []
        for row_index in range(sheet.nrows):
            if rows_read >= MAX_SPREADSHEET_ROWS:
                break
            values = [str(cell).strip() for cell in sheet.row_values(row_index)]
            line = " | ".join(value for value in values if value)
            if line:
                sheet_lines.append(line)
            rows_read += 1
        if sheet_lines:
            blocks.append(f"[{sheet.name}]\n" + "\n".join(sheet_lines))
        if rows_read >= MAX_SPREADSHEET_ROWS:
            break

    return "\n\n".join(blocks).strip()


def _read_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")
