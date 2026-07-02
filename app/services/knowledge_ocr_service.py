"""Optional OCR helpers for Knowledge attachments."""

from __future__ import annotations

import importlib
import logging
import base64
import json
import mimetypes
import re
import tempfile
from dataclasses import dataclass
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
OCR_CONFIGS = ("--oem 3 --psm 6", "--oem 3 --psm 11", "--oem 3 --psm 4", "--oem 3 --psm 12")
OCR_ROTATIONS = (0, 90, 180, 270)


@dataclass(frozen=True)
class OcrResult:
    text: str = ""
    mode: str = ""
    rotation: int = 0
    chars: int = 0
    words: int = 0
    barcode_text: str = ""

    def score(self) -> tuple[int, int]:
        return self.words, self.chars


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



def _json_from_string(value: str) -> object | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            return None
    return None


def extract_ai_ocr_text(response: object) -> str:
    """Extract useful OCR text from common AI response shapes."""
    seen: set[int] = set()

    def visit(value: object, depth: int = 0) -> str:
        if value is None or depth > 6:
            return ""
        value_id = id(value)
        if value_id in seen:
            return ""
        seen.add(value_id)
        if isinstance(value, str):
            parsed = _json_from_string(value)
            if parsed is not None and parsed is not value:
                nested = visit(parsed, depth + 1)
                if nested:
                    return nested
            return _clean_text(value)
        if isinstance(value, dict):
            for key in ("text", "texto", "answer", "content", "output_text"):
                if key in value:
                    text = visit(value.get(key), depth + 1)
                    if text:
                        return text
            for key in ("message", "data", "result", "response", "output"):
                if key in value:
                    text = visit(value.get(key), depth + 1)
                    if text:
                        return text
            return ""
        if isinstance(value, (list, tuple)):
            parts = [visit(item, depth + 1) for item in value]
            return _clean_text("\n".join(part for part in parts if part))
        for attr in ("output_text", "text", "texto", "answer", "content"):
            if hasattr(value, attr):
                text = visit(getattr(value, attr), depth + 1)
                if text:
                    return text
        if hasattr(value, "output"):
            text = visit(getattr(value, "output"), depth + 1)
            if text:
                return text
        return ""

    text = visit(response)
    return text if any(char.isalnum() for char in text) else ""


def evaluate_ocr_quality(text: str, file_path: str | Path | None = None) -> dict[str, object]:
    """Conservative local OCR quality heuristic for hybrid OCR decisions."""
    cleaned = _clean_text(text)
    useful_chars = sum(1 for char in cleaned if char.isalnum())
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{2,}", cleaned)
    real_words = [word for word in words if any(char.isalpha() for char in word)]
    symbols = sum(1 for char in cleaned if not char.isalnum() and not char.isspace())
    short_lines = sum(1 for line in cleaned.splitlines() if 0 < sum(1 for char in line if char.isalnum()) <= 2)
    lines = max(1, len([line for line in cleaned.splitlines() if line.strip()]))
    symbol_ratio = symbols / max(1, len(cleaned))
    short_line_ratio = short_lines / lines
    score = 0.0
    if useful_chars >= 80:
        score += 0.35
    elif useful_chars >= 40:
        score += 0.2
    if len(real_words) >= 8:
        score += 0.35
    elif len(real_words) >= 4:
        score += 0.2
    if symbol_ratio <= 0.18:
        score += 0.15
    if short_line_ratio <= 0.35:
        score += 0.1
    if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d+[,.]\d{2}\b|\b\+?\d{6,}\b", cleaned):
        score += 0.05
    score = round(min(score, 1.0), 2)
    if useful_chars < 20:
        quality, reason = "empty", "menos de 20 caracteres útiles"
        score = 0.0
    elif useful_chars < 60 or len(real_words) < 5 or symbol_ratio > 0.35 or short_line_ratio > 0.55 or score < 0.65:
        quality, reason = "low_quality", f"texto insuficiente o ruidoso (chars={useful_chars}, palabras={len(real_words)}, símbolos={symbol_ratio:.0%})"
    else:
        quality, reason = "ok", "texto suficiente para indexación local"
    return {"quality": quality, "is_good_enough": quality == "ok", "score": int(round(score * 100)), "reason": reason, "chars": useful_chars, "words": len(real_words), "file_path": str(file_path or "")}


def _render_pdf_first_page_data_url(path: str) -> str:
    fitz = importlib.import_module("fitz")
    with fitz.open(path) as document:
        if len(document) == 0:
            return ""
        pix = document.load_page(0).get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii")


def _file_data_url(path: str, mime: str) -> str:
    data = Path(path).read_bytes()
    return f"data:{mime or 'image/png'};base64," + base64.b64encode(data).decode("ascii")


def improve_ocr_with_ai(attachment_path: str, mime: str, current_ocr_text: str | None = None) -> dict[str, object]:
    """Use configured visual AI to extract better OCR text from an image or first PDF page."""
    prompt = (
        "Analiza esta imagen/documento. Extrae todo el texto legible. Corrige errores de OCR evidentes. "
        "Si identificas campos como empresa, fecha, importe, matrícula, medida, pedido, dirección o teléfono, inclúyelos. "
        "No inventes datos no visibles. Devuelve JSON con text, document_type, fields y confidence."
    )
    try:
        from app.core.openai_client import build_openai_client
        from app.services.openai_service import OpenAIService

        data_url = _render_pdf_first_page_data_url(attachment_path) if is_pdf_candidate(attachment_path, mime) else _file_data_url(attachment_path, mime or "image/png")
        if not data_url:
            return {"ok": False, "status": "empty", "message": "No se pudo renderizar el documento para IA."}
        logger.info("KNOWLEDGE_OCR_AI: requested path=%s", attachment_path)
        client = build_openai_client()
        response = client.responses.create(
            model="gpt-4o-mini",
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": prompt + (f"\nOCR local actual:\n{current_ocr_text}" if current_ocr_text else "")},
                {"type": "input_image", "image_url": data_url},
            ]}],
            text={"format": {"type": "json_object"}},
        )
        logger.info("KNOWLEDGE_OCR_AI: raw_response_type=%s", type(response).__name__)
        raw = OpenAIService._extract_text(response)
        parsed = _json_from_string(raw) if raw else None
        parsed_dict = parsed if isinstance(parsed, dict) else {}
        text = extract_ai_ocr_text(parsed_dict or raw or response)
        logger.info("KNOWLEDGE_OCR_AI: extracted_chars=%s preview=%r", len(text), text[:300])
        if not text:
            logger.info("KNOWLEDGE_OCR_AI: empty reason=no_useful_text")
            return {"ok": False, "status": "empty_ai", "text": "", "message": "La IA no ha podido extraer texto útil."}
        return {"ok": True, "text": text, "document_type": parsed_dict.get("document_type", ""), "fields": parsed_dict.get("fields", {}), "confidence": parsed_dict.get("confidence", 0), "status": "ok_ai"}
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR_AI: error reason=%s", exc)
        return {"ok": False, "status": "error", "message": "La IA no ha podido extraer texto útil."}

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


def _preprocess_image_for_ocr(image: object) -> list[tuple[str, object]]:
    """Return PIL image variants optimized for difficult label/ticket OCR."""
    image_mod = importlib.import_module("PIL.Image")
    image_ops = importlib.import_module("PIL.ImageOps")
    image_enhance = importlib.import_module("PIL.ImageEnhance")
    image_filter = importlib.import_module("PIL.ImageFilter")
    resampling = getattr(image_mod, "Resampling", None)
    resample_filter = getattr(resampling, "LANCZOS", 1) if resampling is not None else 1

    processed = image_ops.exif_transpose(image).convert("L")
    processed = image_ops.autocontrast(processed)
    processed = image_enhance.Contrast(processed).enhance(2.0)
    width, height = processed.size
    shortest = min(width, height)
    scale = 3 if shortest < 900 else 2 if shortest < 1800 else 1
    if scale > 1:
        processed = processed.resize((width * scale, height * scale), resample_filter)
    processed = processed.filter(image_filter.SHARPEN)
    threshold = processed.point(lambda pixel: 255 if pixel > 170 else 0)
    return [("preprocessed-gray", processed), ("preprocessed-threshold", threshold)]


def _ocr_word_count(text: str) -> int:
    return sum(1 for token in text.split() if any(character.isalnum() for character in token))


def _read_optional_barcodes(image: object) -> str:
    """Try optional QR/barcode libraries without making them mandatory."""
    try:
        pyzbar = importlib.import_module("pyzbar.pyzbar")
        decoded = pyzbar.decode(image)
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: barcode skipped reason=%s", exc)
        return ""
    values: list[str] = []
    for code in decoded:
        data = getattr(code, "data", b"")
        try:
            value = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        except Exception:  # noqa: BLE001
            value = str(data)
        if value and value not in values:
            values.append(value)
    return _clean_text("\n".join(values))


def _run_tesseract_saved_image(image: object, lang: str, config: str) -> str:
    with tempfile.NamedTemporaryFile(prefix="knowledge_ocr_", suffix=".png", delete=True) as temp_file:
        image.save(temp_file.name)
        return _clean_text(pytesseract.image_to_string(temp_file.name, lang=lang, config=config))


def _tesseract_image_to_result(image: object, lang: str) -> OcrResult:
    best = OcrResult()
    barcode_text = _read_optional_barcodes(image)
    for variant_name, processed in _preprocess_image_for_ocr(image):
        for rotation in OCR_ROTATIONS:
            rotated = processed if rotation == 0 else processed.rotate(rotation, expand=True)
            for config in OCR_CONFIGS:
                try:
                    text = _run_tesseract_saved_image(rotated, lang, config)
                except Exception as exc:  # noqa: BLE001
                    logger.info("KNOWLEDGE_OCR: tesseract config failed config=%s rotation=%s reason=%s", config, rotation, exc)
                    continue
                chars = len(text)
                words = _ocr_word_count(text)
                candidate = OcrResult(text=text, mode=f"{variant_name} {config}", rotation=rotation, chars=chars, words=words)
                if candidate.score() > best.score():
                    best = candidate
    if barcode_text:
        combined = _clean_text(f"{best.text}\n\nQR/Código de barras:\n{barcode_text}")
        best = OcrResult(
            text=combined,
            mode=best.mode,
            rotation=best.rotation,
            chars=len(combined),
            words=_ocr_word_count(combined),
            barcode_text=barcode_text,
        )
    logger.info("KNOWLEDGE_OCR: best mode=%s rotation=%s chars=%s words=%s", best.mode, best.rotation, best.chars, best.words)
    return best


def _tesseract_image_to_string(image: object, lang: str) -> str:
    return _tesseract_image_to_result(image, lang).text


def ocr_image_result(path: str, lang: str = "spa+eng") -> OcrResult:
    available, reason = is_ocr_available()
    if not available:
        logger.info("KNOWLEDGE_OCR: skipped reason=%s", reason)
        return OcrResult()
    try:
        logger.info("KNOWLEDGE_OCR: image started path=%s", path)
        image_mod = importlib.import_module("PIL.Image")
        with image_mod.open(path) as image:
            result = _tesseract_image_to_result(image, lang)
        logger.info("KNOWLEDGE_OCR: image finished chars=%s mode=%s rotation=%s", result.chars, result.mode, result.rotation)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: error reason=%s", exc)
        return OcrResult()


def ocr_image(path: str, lang: str = "spa+eng") -> str:
    return ocr_image_result(path, lang).text


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
                page_result = _tesseract_image_to_result(image, lang)
                page_text = _clean_text(page_result.text)
                logger.info("KNOWLEDGE_OCR: pdf page=%s chars=%s", page_index + 1, len(page_text))
                if page_text:
                    chunks.append(page_text)
                if sum(len(chunk) for chunk in chunks) >= MAX_OCR_TEXT_CHARS:
                    break
        return _clean_text("\n".join(chunks))
    except Exception as exc:  # noqa: BLE001
        logger.info("KNOWLEDGE_OCR: error reason=%s", exc)
        return ""
