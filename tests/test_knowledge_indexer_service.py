import importlib.util

import pytest

from app.services.knowledge_indexer_service import build_indexed_text, extract_text_from_attachment


def test_extract_text_from_txt_with_fallback_encoding(tmp_path) -> None:
    path = tmp_path / "nota.csv"
    path.write_bytes("café;magnetismo".encode("cp1252"))

    assert "magnetismo" in extract_text_from_attachment(path, "text/csv", path.name)


@pytest.mark.skipif(importlib.util.find_spec("fitz") is None, reason="PyMuPDF no disponible")
def test_extract_text_from_pdf(tmp_path) -> None:
    fitz = __import__("fitz")
    path = tmp_path / "busqueda_pdf.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Texto PDF con palabra astrolabio")
    document.save(path)
    document.close()

    assert "astrolabio" in extract_text_from_attachment(path, "application/pdf", path.name)


@pytest.mark.skipif(importlib.util.find_spec("docx") is None, reason="python-docx no disponible")
def test_extract_text_from_docx(tmp_path) -> None:
    docx = __import__("docx")
    path = tmp_path / "busqueda_docx.docx"
    document = docx.Document()
    document.add_paragraph("Documento DOCX con palabra heliostato")
    document.save(path)

    assert "heliostato" in extract_text_from_attachment(path, "", path.name)


@pytest.mark.skipif(importlib.util.find_spec("openpyxl") is None, reason="openpyxl no disponible")
def test_extract_text_from_xlsx(tmp_path) -> None:
    openpyxl = __import__("openpyxl")
    path = tmp_path / "busqueda_xlsx.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Datos"
    sheet["A1"] = "inventario"
    sheet["B1"] = "selenita"
    workbook.save(path)
    workbook.close()

    assert "selenita" in extract_text_from_attachment(path, "", path.name)


def test_build_indexed_text_keeps_unsupported_attachment_name_and_mime(tmp_path) -> None:
    image = tmp_path / "foto_indice.png"
    image.write_bytes(b"not a real image")

    indexed = build_indexed_text(
        {"id": 1, "title": "Nota", "content": "Contenido", "tags": ["tag"]},
        [{"original_filename": image.name, "stored_path": str(image), "mime_type": "image/png"}],
    )

    assert "foto_indice.png" in indexed
    assert "image/png" in indexed


def test_build_indexed_text_adds_normalized_ocr_without_replacing_original() -> None:
    indexed = build_indexed_text(
        {"id": 1, "title": "Ticket", "content": ""},
        [{"original_filename": "Snapshot.png", "ocr_text": "JOSE MIGUEL CARNICER1A"}],
    )

    assert "[OCR: Snapshot.png]\nJOSE MIGUEL CARNICER1A" in indexed
    assert "[OCR_NORMALIZADO: Snapshot.png]" in indexed
    assert "jose miguel carniceria" in indexed


def test_build_indexed_text_prefers_corrected_ocr_over_raw() -> None:
    indexed = build_indexed_text(
        {"id": 1, "title": "Ticket", "content": ""},
        [
            {
                "original_filename": "Snapshot.png",
                "ocr_text": "JOSE MIGUEL CARNICERÍ",
                "ocr_text_raw": "JOSE MIGUEL CARNICERÍ",
                "ocr_text_corrected": "JOSE MIGUEL CARNICERÍA",
            }
        ],
    )

    assert "[OCR corregido: Snapshot.png]\nJOSE MIGUEL CARNICERÍA" in indexed
    assert "[OCR corregido_NORMALIZADO: Snapshot.png]" in indexed
    assert "jose miguel carniceria" in indexed
