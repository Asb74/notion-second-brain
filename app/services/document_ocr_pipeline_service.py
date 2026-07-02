"""Advanced shared OCR pipeline for Knowledge document attachments.

All OCR-capable attachment sources should delegate here.  The service keeps the
importers/UI thin: it detects file type, renders scanned PDFs, applies document
pre-processing with OpenCV, runs several local Tesseract variants, evaluates
quality, persists the result in Knowledge SQLite, and only *marks* weak results
as AI candidates without invoking AI automatically.
"""

from __future__ import annotations

import importlib
import logging
import mimetypes
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:  # Optional at import time so the app can still start and report runtime status.
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None  # type: ignore[assignment]

try:
    import pytesseract  # type: ignore
except Exception:  # noqa: BLE001
    pytesseract = None  # type: ignore[assignment]

from app.services.knowledge_ocr_service import (
    IMAGE_EXTENSIONS,
    IMAGE_MIME_TYPES,
    MAX_OCR_TEXT_CHARS,
    PDF_EXTENSIONS,
    _available_tesseract_langs,
    _clean_text,
    _ocr_word_count,
    evaluate_ocr_quality as legacy_evaluate_ocr_quality,
    is_ocr_available,
)

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS
SUPPORTED_PDF_EXTENSIONS = PDF_EXTENSIONS
PDF_EMBEDDED_TEXT_THRESHOLD = 80
PDF_RENDER_DPI = 300
OCR_LANGUAGES = ("spa+eng", "spa", "eng")
OCR_PSMS = (6, 11, 12)
MAX_PDF_PAGES = 25


@dataclass
class DocumentOcrResult:
    text: str = ""
    status: str = "empty"
    engine: str = "local"
    mode: str = ""
    score: int = 0
    quality: dict[str, Any] = field(default_factory=dict)
    chars: int = 0
    words: int = 0
    language: str = ""
    psm: int | None = None
    rotation: int = 0
    page: int | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)
    detected_document: bool = False
    embedded_text_used: bool = False
    ai_candidate: bool = False
    preview: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["ok"] = self.status == "ok_local"
        return data


class DocumentOcrPipelineService:
    """Single advanced OCR pipeline for images and PDFs from every source."""

    def __init__(self, repository: Any | None = None, debug: bool | None = None) -> None:
        self.repository = repository
        self.debug = bool(debug if debug is not None else os.environ.get("NEXUS_OCR_DEBUG"))
        self.debug_dir = Path("logs/ocr_debug")

    def process_attachment(self, attachment: Any, note_id: int | None = None) -> dict[str, Any]:
        attachment_id = self._value(attachment, "id") or self._value(attachment, "attachment_id")
        path = str(self._value(attachment, "stored_path") or self._value(attachment, "file_path") or self._value(attachment, "local_path") or "")
        mime = str(self._value(attachment, "mime_type") or self._value(attachment, "mime") or mimetypes.guess_type(path)[0] or "")
        if note_id is None:
            note_id = self._int_or_none(self._value(attachment, "item_id") or self._value(attachment, "note_id"))
        metadata = {"attachment_id": self._int_or_none(attachment_id), "note_id": note_id, "mime_type": mime}
        logger.info("DOCUMENT_OCR_PIPELINE: attachment started attachment_id=%s note_id=%s path=%s mime=%s", attachment_id, note_id, path, mime)
        if self._is_pdf(path, mime):
            result = self.process_pdf_file(path, metadata)
        elif self._is_image(path, mime):
            result = self.process_image_file(path, metadata)
        else:
            result = DocumentOcrResult(status="skipped", error="Tipo de adjunto no soportado")
        return self.save_ocr_result(note_id, self._int_or_none(attachment_id), result)

    def process_image_file(self, path: str | Path, metadata: dict[str, Any] | None = None) -> DocumentOcrResult:
        start = time.perf_counter()
        source = Path(path)
        logger.info("DOCUMENT_OCR_PIPELINE: image file=%s type=image", source)
        try:
            processed, detected = self.preprocess_document_image(source)
            result = self.run_tesseract_variants(processed)
            result.detected_document = detected
            result.quality = self.evaluate_ocr_quality(result.text, metadata)
            result.score = int(result.quality.get("score") or 0)
            result.status = "ok_local" if result.quality.get("is_good_enough") else ("empty" if not result.text.strip() else "low_quality")
            result.ai_candidate = self.build_ai_candidate_if_needed(result)
            result.preview = result.text[:300]
            logger.info("DOCUMENT_OCR_PIPELINE: image finished file=%s detected_document=%s dims=%s score=%s status=%s best=%s elapsed_ms=%s", source, detected, getattr(processed, "size", None), result.score, result.status, result.mode, int((time.perf_counter() - start) * 1000))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("DOCUMENT_OCR_PIPELINE: image error file=%s", source)
            return DocumentOcrResult(status="error", error=str(exc))

    def process_pdf_file(self, path: str | Path, metadata: dict[str, Any] | None = None) -> DocumentOcrResult:
        source = Path(path)
        logger.info("DOCUMENT_OCR_PIPELINE: pdf file=%s type=pdf", source)
        embedded = self._extract_pdf_text(source)
        embedded_quality = self.evaluate_ocr_quality(embedded, metadata) if embedded else {"is_good_enough": False, "score": 0}
        logger.info("DOCUMENT_OCR_PIPELINE: pdf embedded_text=%s chars=%s score=%s", bool(len(embedded) >= PDF_EMBEDDED_TEXT_THRESHOLD), len(embedded), embedded_quality.get("score"))
        if len(embedded) >= PDF_EMBEDDED_TEXT_THRESHOLD and embedded_quality.get("is_good_enough"):
            result = DocumentOcrResult(text=embedded, status="ok_local", mode="pdf_embedded_text", chars=len(embedded), words=_ocr_word_count(embedded), embedded_text_used=True, quality=embedded_quality, score=int(embedded_quality.get("score") or 0), preview=embedded[:300])
            result.ai_candidate = False
            return result
        pages = self.render_pdf_pages(source)
        chunks: list[str] = []
        page_results: list[dict[str, Any]] = []
        for page_no, image_path in pages:
            page = self.process_image_file(image_path, {**(metadata or {}), "page": page_no})
            page.page = page_no
            page_text = _clean_text(page.text)
            if page_text:
                chunks.append(f"[PDF página {page_no}]\n{page_text}")
            page_results.append(page.as_dict())
            try:
                Path(image_path).unlink(missing_ok=True)
            except Exception:
                pass
            if sum(len(chunk) for chunk in chunks) >= MAX_OCR_TEXT_CHARS:
                break
        text = _clean_text("\n\n".join(chunks))
        quality = self.evaluate_ocr_quality(text, metadata)
        status = "ok_local" if quality.get("is_good_enough") else ("empty" if not text else "low_quality")
        result = DocumentOcrResult(text=text, status=status, mode="pdf_rendered_document_ocr", chars=len(text), words=_ocr_word_count(text), pages=page_results, quality=quality, score=int(quality.get("score") or 0), preview=text[:300])
        result.ai_candidate = self.build_ai_candidate_if_needed(result)
        logger.info("DOCUMENT_OCR_PIPELINE: pdf rendered pages=%s chars=%s score=%s status=%s ai_candidate=%s", len(pages), len(text), result.score, status, result.ai_candidate)
        return result

    def render_pdf_pages(self, path: str | Path) -> list[tuple[int, str]]:
        fitz = importlib.import_module("fitz")
        rendered: list[tuple[int, str]] = []
        zoom = PDF_RENDER_DPI / 72
        with fitz.open(path) as document:
            total = min(len(document), MAX_PDF_PAGES)
            logger.info("DOCUMENT_OCR_PIPELINE: pdf render pages=%s dpi=%s", total, PDF_RENDER_DPI)
            for idx in range(total):
                pix = document.load_page(idx).get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                temp = tempfile.NamedTemporaryFile(prefix=f"nexus_pdf_ocr_p{idx+1}_", suffix=".png", delete=False)
                temp.close()
                pix.save(temp.name)
                rendered.append((idx + 1, temp.name))
        return rendered

    def preprocess_document_image(self, image_path: str | Path) -> tuple[Image.Image, bool]:
        pil = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        original_size = pil.size
        if cv2 is None:
            logger.info("DOCUMENT_OCR_PIPELINE: opencv unavailable; using PIL enhancement")
            return self.enhance_for_ocr(pil), False
        image = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        contour = self.detect_document_contour(image)
        detected = contour is not None
        warped = self.correct_perspective(image, contour) if detected else image
        enhanced = self.enhance_for_ocr(warped)
        logger.info("DOCUMENT_OCR_PIPELINE: preprocess dims_before=%s dims_after=%s detected_document=%s", original_size, enhanced.size, detected)
        return enhanced, detected

    def detect_document_contour(self, image: Any) -> Any | None:
        if cv2 is None:
            return None
        ratio = image.shape[0] / 900.0 if image.shape[0] > 900 else 1.0
        resized = cv2.resize(image, (int(image.shape[1] / ratio), int(image.shape[0] / ratio))) if ratio != 1.0 else image.copy()
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            area = cv2.contourArea(approx)
            if len(approx) == 4 and area > resized.shape[0] * resized.shape[1] * 0.18:
                return (approx.reshape(4, 2) * ratio).astype("float32")
        return None

    def correct_perspective(self, image: Any, contour: Any) -> Any:
        if cv2 is None or contour is None:
            return image
        rect = self._order_points(contour)
        (tl, tr, br, bl) = rect
        width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32")
        matrix = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, matrix, (width, height))

    def enhance_for_ocr(self, image: Any) -> Image.Image:
        if isinstance(image, Image.Image):
            pil = image.convert("RGB")
            gray = ImageOps.grayscale(pil)
        else:
            gray_cv = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if cv2 is not None else image
            if cv2 is not None:
                background = cv2.medianBlur(gray_cv, 31)
                normalized = cv2.divide(gray_cv, background, scale=255)
                normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(normalized)
                binary = cv2.adaptiveThreshold(normalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11)
                pil = Image.fromarray(binary)
            else:
                pil = Image.fromarray(gray_cv).convert("L")
            gray = pil
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Contrast(gray).enhance(1.8)
        w, h = gray.size
        scale = 2 if min(w, h) < 1400 else 1
        if scale > 1:
            gray = gray.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
        return gray.filter(ImageFilter.SHARPEN)

    def run_tesseract_variants(self, image: Image.Image) -> DocumentOcrResult:
        available, reason = is_ocr_available()
        if not available or pytesseract is None:
            return DocumentOcrResult(status="unavailable", error=reason)
        best = DocumentOcrResult()
        langs = [lang for lang in _available_tesseract_langs() if lang in {"spa+eng", "spa", "eng"} or lang]
        for rotation in (0, 90, 180, 270):
            rotated = image if rotation == 0 else image.rotate(rotation, expand=True)
            for lang in langs or OCR_LANGUAGES:
                for psm in OCR_PSMS:
                    config = f"--oem 3 --psm {psm}"
                    try:
                        text = _clean_text(pytesseract.image_to_string(rotated, lang=lang, config=config))
                    except Exception as exc:  # noqa: BLE001
                        logger.info("DOCUMENT_OCR_PIPELINE: tesseract failed lang=%s psm=%s rotation=%s error=%s", lang, psm, rotation, exc)
                        continue
                    quality = self.evaluate_ocr_quality(text)
                    score = int(quality.get("score") or 0)
                    useful = int(quality.get("chars") or 0)
                    logger.info("DOCUMENT_OCR_PIPELINE: tesseract tried lang=%s psm=%s rotation=%s useful_chars=%s score=%s preview=%r", lang, psm, rotation, useful, score, text[:120])
                    if (score, useful, len(text)) > (best.score, best.chars, len(best.text)):
                        best = DocumentOcrResult(text=text, mode=f"document_preprocess lang={lang} psm={psm}", score=score, quality=quality, chars=useful, words=int(quality.get("words") or 0), language=lang, psm=psm, rotation=rotation, preview=text[:300])
        logger.info("DOCUMENT_OCR_PIPELINE: tesseract best lang=%s psm=%s rotation=%s chars=%s score=%s", best.language, best.psm, best.rotation, best.chars, best.score)
        return best

    def evaluate_ocr_quality(self, text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        quality = legacy_evaluate_ocr_quality(text, (metadata or {}).get("file_path"))
        cleaned = _clean_text(text)
        quality["emails"] = re.findall(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", cleaned, re.I)
        quality["phones"] = re.findall(r"(?<!\d)(?:\+?34[\s.-]?)?(?:[689]\d|9\d{1})[\d\s().-]{6,}\d", cleaned)
        quality["urls"] = re.findall(r"\b(?:https?://|www\.)\S+", cleaned, re.I)
        return quality

    def save_ocr_result(self, note_id: int | None, attachment_id: int | None, result: DocumentOcrResult) -> dict[str, Any]:
        if self.repository is None or not attachment_id:
            return result.as_dict()
        try:
            status = result.status or "empty"
            self.repository.update_attachment_ocr(attachment_id, result.text, status, ocr_mode=result.mode, ocr_rotation=result.rotation, ocr_characters=len(result.text))
            self.repository.conn.execute(
                "UPDATE knowledge_attachments SET ocr_quality_score = ?, ocr_quality_reason = ?, ocr_engine = ?, ai_ocr_status = ? WHERE id = ?",
                (float(result.score or 0), str(result.quality.get("reason") or result.error or ""), result.engine, "candidate" if result.ai_candidate else "", attachment_id),
            )
            self.repository.conn.commit()
            if note_id and status == "ok_local":
                self.repository.reindex_item(note_id)
            logger.info("DOCUMENT_OCR_PIPELINE: sqlite saved note_id=%s attachment_id=%s status=%s score=%s ai_candidate=%s", note_id, attachment_id, status, result.score, result.ai_candidate)
        except Exception as exc:  # noqa: BLE001
            logger.exception("DOCUMENT_OCR_PIPELINE: sqlite save error attachment_id=%s", attachment_id)
            result.error = str(exc)
        return result.as_dict() | {"attachment_id": attachment_id, "note_id": note_id, "candidate_ai": result.ai_candidate}

    def build_ai_candidate_if_needed(self, result: DocumentOcrResult) -> bool:
        return not bool(result.quality.get("is_good_enough"))

    def _extract_pdf_text(self, path: Path) -> str:
        try:
            fitz = importlib.import_module("fitz")
            with fitz.open(path) as document:
                return _clean_text("\n".join(document.load_page(i).get_text("text") for i in range(min(len(document), MAX_PDF_PAGES))))
        except Exception as exc:  # noqa: BLE001
            logger.info("DOCUMENT_OCR_PIPELINE: pdf embedded extraction failed file=%s error=%s", path, exc)
            return ""

    @staticmethod
    def _order_points(points: Any) -> Any:
        rect = np.zeros((4, 2), dtype="float32")
        s = points.sum(axis=1)
        rect[0] = points[np.argmin(s)]
        rect[2] = points[np.argmax(s)]
        diff = np.diff(points, axis=1)
        rect[1] = points[np.argmin(diff)]
        rect[3] = points[np.argmax(diff)]
        return rect

    @staticmethod
    def _value(row: Any, key: str, default: Any = None) -> Any:
        if row is None:
            return default
        try:
            if hasattr(row, "keys") and key in row.keys():
                return row[key]
            if isinstance(row, dict):
                return row.get(key, default)
            return getattr(row, key, default)
        except Exception:
            return default

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value) if value is not None and str(value) != "" else None
        except Exception:
            return None

    @staticmethod
    def _is_pdf(path: str, mime: str = "") -> bool:
        return Path(path).suffix.lower() in PDF_EXTENSIONS or (mime or "").lower() == "application/pdf"

    @staticmethod
    def _is_image(path: str, mime: str = "") -> bool:
        return Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS or (mime or "").lower() in IMAGE_MIME_TYPES


_default_service = DocumentOcrPipelineService()


def process_attachment(attachment: Any, note_id: int | None = None) -> dict[str, Any]:
    return _default_service.process_attachment(attachment, note_id)


def process_image_file(path: str | Path, metadata: dict[str, Any] | None = None) -> DocumentOcrResult:
    return _default_service.process_image_file(path, metadata)


def process_pdf_file(path: str | Path, metadata: dict[str, Any] | None = None) -> DocumentOcrResult:
    return _default_service.process_pdf_file(path, metadata)
