from app.ui.knowledge_manager_window import KnowledgeManagerWindow


def test_tree_note_text_uses_plain_title_without_type_prefix() -> None:
    assert KnowledgeManagerWindow._tree_note_text("Abono Betis") == "Abono Betis"
    assert KnowledgeManagerWindow._tree_note_text("RV: SANDIA MERCA") == "RV: SANDIA MERCA"
    assert KnowledgeManagerWindow._tree_note_text("20260611 Anotaciones") == "20260611 Anotaciones"


def test_tree_note_text_does_not_add_knowledge_type_prefix() -> None:
    display_title = KnowledgeManagerWindow._tree_note_text("Abono Betis")

    assert display_title != "[Nota] Abono Betis"
    assert not display_title.startswith("[")
