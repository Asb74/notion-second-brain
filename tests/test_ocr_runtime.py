from pathlib import Path

from app.services import ocr_runtime


def test_detect_tesseract_prefers_user_path(tmp_path: Path) -> None:
    user_dir = tmp_path / "UserTesseract"
    user_dir.mkdir()
    user_exe = user_dir / "tesseract.exe"
    user_exe.write_text("exe", encoding="utf-8")

    bundled_dir = tmp_path / "Tesseract-OCR"
    bundled_dir.mkdir()
    (bundled_dir / "tesseract.exe").write_text("exe", encoding="utf-8")

    detected, source = ocr_runtime.detect_tesseract(str(user_exe), base_dir=tmp_path)

    assert detected == str(user_exe)
    assert source == "user"


def test_detect_tesseract_checks_bundled_internal(tmp_path: Path) -> None:
    bundled_dir = tmp_path / "_internal" / "Tesseract-OCR"
    bundled_dir.mkdir(parents=True)
    bundled_exe = bundled_dir / "tesseract.exe"
    bundled_exe.write_text("exe", encoding="utf-8")

    detected, source = ocr_runtime.detect_tesseract(base_dir=tmp_path)

    assert detected == str(bundled_exe)
    assert source == "bundled"


def test_detect_languages_reports_missing_without_failing(tmp_path: Path) -> None:
    tesseract_dir = tmp_path / "Tesseract-OCR"
    tessdata = tesseract_dir / "tessdata"
    tessdata.mkdir(parents=True)
    tesseract_exe = tesseract_dir / "tesseract.exe"
    tesseract_exe.write_text("exe", encoding="utf-8")
    (tessdata / "spa.traineddata").write_text("spa", encoding="utf-8")

    available, missing = ocr_runtime.detect_languages(str(tesseract_exe))

    assert available == ("spa",)
    assert missing == ("eng",)
