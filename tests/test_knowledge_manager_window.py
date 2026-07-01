from app.ui.knowledge_manager_window import KnowledgeManagerWindow


def test_tree_note_text_uses_plain_title_without_type_prefix() -> None:
    assert KnowledgeManagerWindow._tree_note_text("Abono Betis") == "Abono Betis"
    assert KnowledgeManagerWindow._tree_note_text("RV: SANDIA MERCA") == "RV: SANDIA MERCA"
    assert KnowledgeManagerWindow._tree_note_text("20260611 Anotaciones") == "20260611 Anotaciones"


def test_tree_note_text_does_not_add_knowledge_type_prefix() -> None:
    display_title = KnowledgeManagerWindow._tree_note_text("Abono Betis")

    assert display_title != "[Nota] Abono Betis"
    assert not display_title.startswith("[")


def test_format_ocr_status_accepts_dicts_and_missing_values() -> None:
    window = KnowledgeManagerWindow.__new__(KnowledgeManagerWindow)

    assert window._format_ocr_status({"ocr_status": "ok"}) == "ok"
    assert window._format_ocr_status({"ocr_status": "pending"}) == "pendiente"
    assert window._format_ocr_status({"ocr_status": "empty"}) == "sin texto"
    assert window._format_ocr_status({"ocr_status": "error"}) == "error"
    assert window._format_ocr_status({"ocr_status": None}) == ""
    assert window._format_ocr_status({"id": 1}) == ""


def test_format_ocr_status_accepts_objects_without_ocr_fields() -> None:
    window = KnowledgeManagerWindow.__new__(KnowledgeManagerWindow)

    class Attachment:
        pass

    assert window._format_ocr_status(Attachment()) == ""


def test_format_ocr_status_accepts_sqlite_rows_with_missing_ocr_status() -> None:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT 1 AS id").fetchone()
    window = KnowledgeManagerWindow.__new__(KnowledgeManagerWindow)

    assert window._format_ocr_status(row) == ""
