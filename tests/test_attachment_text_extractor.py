from __future__ import annotations

from pathlib import Path

from app.utils import attachment_text_extractor as extractor


def test_extract_text_from_attachment_txt(tmp_path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hola mundo", encoding="utf-8")

    assert extractor.extract_text_from_attachment(str(file_path)) == "hola mundo"


def test_extract_text_from_attachment_unsupported_returns_empty(tmp_path) -> None:
    file_path = tmp_path / "image.png"
    file_path.write_bytes(b"PNG")

    assert extractor.extract_text_from_attachment(str(file_path)) == ""


def test_extract_text_from_attachment_uses_filename_extension(tmp_path) -> None:
    file_path = tmp_path / "binary_attachment"
    file_path.write_text("contenido valido", encoding="utf-8")

    assert extractor.extract_text_from_attachment(str(file_path), filename="PK 881 EDEKA.xlsx") == ""
    assert extractor.extract_text_from_attachment(str(file_path), filename="nota.txt") == "contenido valido"


def test_extract_text_from_attachments_combines_and_truncates(monkeypatch) -> None:
    monkeypatch.setattr(extractor, "MAX_ATTACHMENT_TEXT", 20)
    monkeypatch.setattr(extractor, "extract_text_from_attachment", lambda *_args, **_kwargs: "x" * 30)

    text = extractor.extract_text_from_attachments(
        [{"file_path": "/tmp/a", "filename": "a.txt", "mime_type": "application/octet-stream"}]
    )

    assert len(text) == 20
    assert text.startswith("ATTACHMENT: a.txt")


def test_extract_pdf_runs_ocr_when_text_is_too_short(monkeypatch, caplog) -> None:
    monkeypatch.setattr(extractor, "PDF_OCR_MIN_TEXT_LENGTH", 50)

    class _ReaderPage:
        def extract_text(self) -> str:
            return ""

    class _Reader:
        pages = [_ReaderPage()]

    class _PdfPlumberPage:
        def extract_text(self) -> str:
            return ""

    class _PdfPlumberDoc:
        pages = [_PdfPlumberPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class _FitzDoc:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def __iter__(self):
            return iter([])

    monkeypatch.setitem(
        __import__("sys").modules,
        "pdfplumber",
        type("_PdfPlumberModule", (), {"open": lambda *_args, **_kwargs: _PdfPlumberDoc()})(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "PyPDF2",
        type("_PyPdf2Module", (), {"PdfReader": lambda *_args, **_kwargs: _Reader()})(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "fitz",
        type("_FitzModule", (), {"open": lambda *_args, **_kwargs: _FitzDoc()})(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "pdfminer.high_level",
        type("_PdfMinerModule", (), {"extract_text": lambda *_args, **_kwargs: "tiny"})(),
    )
    monkeypatch.setattr(extractor, "_extract_pdf_with_ocr", lambda _path: "ocr extracted text")

    caplog.set_level("INFO")
    text = extractor._extract_pdf(Path("dummy.pdf"))

    assert text == "tiny\n\nocr extracted text"
    assert "PDF contained no text, running OCR fallback" in caplog.text


def test_extract_pdf_skips_ocr_when_text_is_sufficient(monkeypatch) -> None:
    monkeypatch.setattr(extractor, "PDF_OCR_MIN_TEXT_LENGTH", 50)
    monkeypatch.setitem(
        __import__("sys").modules,
        "pdfplumber",
        type(
            "_PdfPlumberModule",
            (),
            {
                "open": lambda *_args, **_kwargs: type(
                    "_PdfPlumberDoc",
                    (),
                    {
                        "pages": [
                            type("_PdfPlumberPage", (), {"extract_text": lambda *_a, **_k: "x" * 60})()
                        ],
                        "__enter__": lambda self: self,
                        "__exit__": lambda self, *_args: None,
                    },
                )()
            },
        )(),
    )

    called = {"ocr": False}

    def _ocr(_path):
        called["ocr"] = True
        return "ocr"

    monkeypatch.setattr(extractor, "_extract_pdf_with_ocr", _ocr)

    text = extractor._extract_pdf(Path("dummy.pdf"))

    assert len(text) >= 60
    assert called["ocr"] is False
