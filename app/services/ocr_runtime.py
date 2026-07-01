"""Centralized Tesseract OCR runtime detection and pytesseract configuration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import sys
from typing import Iterable

from app.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

REQUIRED_LANGUAGES: tuple[str, ...] = ("spa", "eng")
_WINDOWS_TESSERACT_PATHS: tuple[Path, ...] = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


@dataclass(frozen=True, slots=True)
class OcrRuntimeStatus:
    """Current OCR runtime availability and language details."""

    available: bool
    tesseract_path: str = ""
    source: str = ""
    languages_available: tuple[str, ...] = ()
    languages_missing: tuple[str, ...] = REQUIRED_LANGUAGES
    reason: str = ""

    @property
    def languages_ok(self) -> bool:
        return not self.languages_missing


def app_dir() -> Path:
    """Return the directory where bundled external application files live."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_tesseract_candidates(base_dir: Path | None = None) -> tuple[Path, ...]:
    """Return Tesseract candidates bundled next to the app/executable."""
    root = base_dir or app_dir()
    candidates = [
        root / "Tesseract-OCR" / "tesseract.exe",
        root / "_internal" / "Tesseract-OCR" / "tesseract.exe",
    ]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "Tesseract-OCR" / "tesseract.exe")
    return tuple(candidates)


def get_configured_tesseract_path(config_manager: ConfigManager | None = None) -> str:
    config = (config_manager or ConfigManager()).load()
    ocr_settings = config.get("ocr_settings", {})
    if not isinstance(ocr_settings, dict):
        return ""
    return str(ocr_settings.get("tesseract_path", "") or "").strip()


def set_configured_tesseract_path(path: str, config_manager: ConfigManager | None = None) -> None:
    manager = config_manager or ConfigManager()
    config = manager.load()
    config["ocr_settings"] = {"tesseract_path": str(path or "").strip()}
    manager.save(config)


def iter_tesseract_candidates(user_path: str = "", base_dir: Path | None = None) -> Iterable[tuple[str, Path]]:
    if user_path:
        yield "user", Path(user_path)
    for candidate in bundled_tesseract_candidates(base_dir):
        yield "bundled", candidate
    for candidate in _WINDOWS_TESSERACT_PATHS:
        yield "system", candidate
    path_candidate = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if path_candidate:
        yield "path", Path(path_candidate)


def detect_tesseract(user_path: str = "", base_dir: Path | None = None) -> tuple[str, str]:
    """Return detected tesseract path and source, or empty strings if unavailable."""
    seen: set[str] = set()
    for source, candidate in iter_tesseract_candidates(user_path, base_dir):
        normalized = str(candidate).strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        if candidate.is_file():
            return str(candidate), source
    return "", ""


def detect_languages(tesseract_path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Detect bundled/installed tessdata files without invoking Tesseract."""
    if not tesseract_path:
        return (), REQUIRED_LANGUAGES
    tessdata_dir = Path(tesseract_path).resolve().parent / "tessdata"
    available = tuple(lang for lang in REQUIRED_LANGUAGES if (tessdata_dir / f"{lang}.traineddata").is_file())
    missing = tuple(lang for lang in REQUIRED_LANGUAGES if lang not in available)
    logger.info("OCR_RUNTIME: languages detected=%s missing=%s tessdata=%s", ",".join(available) or "none", ",".join(missing) or "none", tessdata_dir)
    return available, missing


def configure_pytesseract(user_path: str = "", *, config_manager: ConfigManager | None = None) -> OcrRuntimeStatus:
    """Detect Tesseract, configure pytesseract, and report runtime status."""
    try:
        import pytesseract  # type: ignore
    except Exception as exc:  # noqa: BLE001
        reason = f"pytesseract unavailable: {exc}"
        logger.info("OCR_RUNTIME: tesseract unavailable reason=%s", reason)
        return OcrRuntimeStatus(False, reason=reason)

    configured_path = user_path.strip() or get_configured_tesseract_path(config_manager)
    tesseract_path, source = detect_tesseract(configured_path)
    if not tesseract_path:
        reason = "tesseract.exe not found"
        logger.info("OCR_RUNTIME: tesseract unavailable reason=%s", reason)
        return OcrRuntimeStatus(False, reason=reason)

    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    languages_available, languages_missing = detect_languages(tesseract_path)
    logger.info("OCR_RUNTIME: tesseract found path=%s source=%s", tesseract_path, source)
    if languages_missing:
        logger.warning("OCR_RUNTIME: languages missing=%s", ",".join(languages_missing))
    return OcrRuntimeStatus(
        True,
        tesseract_path=tesseract_path,
        source=source,
        languages_available=languages_available,
        languages_missing=languages_missing,
        reason="ok",
    )


def test_ocr_runtime(user_path: str = "") -> OcrRuntimeStatus:
    """Configure pytesseract and verify Tesseract responds; missing languages only warn."""
    status = configure_pytesseract(user_path)
    if not status.available:
        return status
    try:
        import pytesseract  # type: ignore

        pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        logger.info("OCR_RUNTIME: tesseract unavailable reason=%s", reason)
        return OcrRuntimeStatus(
            False,
            tesseract_path=status.tesseract_path,
            source=status.source,
            languages_available=status.languages_available,
            languages_missing=status.languages_missing,
            reason=reason,
        )
    return status
