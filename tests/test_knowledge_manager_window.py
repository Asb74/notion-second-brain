from app.ui.knowledge_manager_window import KnowledgeManagerWindow


class _Value:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Text:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self, _start: str, _end: str) -> str:
        return self.value


class _Repo:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updated: list[dict[str, object]] = []

    def create_item(self, **kwargs: object) -> int:
        self.created.append(kwargs)
        return 41

    def update_item(self, **kwargs: object) -> int:
        self.updated.append(kwargs)
        return 1


def _save_window(view: str = "classified") -> KnowledgeManagerWindow:
    window = KnowledgeManagerWindow.__new__(KnowledgeManagerWindow)
    window.current_item_id = None
    window.repo = _Repo()
    window.title_var = _Value("Nota manual")
    window.content_text = _Text("Contenido")
    window.summary_text = _Text("")
    window.area_var = _Value("General")
    window.type_var = _Value("Nota")
    window.topic_var = _Value("")
    window.tags_var = _Value("uno, dos")
    window.source_var = _Value("manual")
    window.inbox_view_var = _Value(view)
    window.status_var = _Value()
    window.areas_by_name = {}
    window.types_by_name = {}
    window.topics_by_name = {}
    window._update_summary_controls = lambda: None
    window._select_saved_item = lambda _item_id: None
    window.refresh_attachments = lambda: None
    window.refresh_note_entities = lambda: None
    return window


def test_save_current_item_creates_in_the_current_view_and_reuses_id() -> None:
    window = _save_window("classified")

    assert window._save_current_item() == 41
    assert window.current_item_id == 41
    assert window.repo.created[0]["inbox_status"] == "classified"

    assert window._save_current_item() == 41
    assert len(window.repo.created) == 1
    assert window.repo.updated[0]["item_id"] == 41


def test_save_current_item_uses_inbox_status_from_inbox_view() -> None:
    window = _save_window("inbox")

    window._save_current_item()

    assert window.repo.created[0]["inbox_status"] == "inbox"


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
