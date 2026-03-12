from __future__ import annotations

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
