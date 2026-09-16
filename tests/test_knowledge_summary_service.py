import pytest

from app.services import knowledge_summary_service as service
from app.services.knowledge_summary_service import (
    KnowledgeSummaryConfigError,
    calculate_summary_source_hash,
    generate_knowledge_summary,
)


class _FakeResponses:
    def __init__(self):
        self.prompt = ""

    def create(self, model: str, input: str):  # noqa: A002 - mirrors OpenAI client API
        self.prompt = input
        return type("Response", (), {"output_text": "Resumen:\nTexto breve"})()


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()


def test_generate_knowledge_summary_uses_note_metadata_and_indexed_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr(service, "build_openai_client", lambda: client)

    result = generate_knowledge_summary(
        {
            "id": 7,
            "title": "Contrato proveedor",
            "area_name": "Sansebas",
            "topic_name": "Legal",
            "item_type_name": "Documento",
            "tags": ["contrato", "proveedor"],
            "content": "Cláusula principal",
            "indexed_text": "Texto extraído de adjunto",
        }
    )

    assert result == "Resumen:\nTexto breve"
    assert "Título: Contrato proveedor" in client.responses.prompt
    assert "Área: Sansebas" in client.responses.prompt
    assert "Tema: Legal" in client.responses.prompt
    assert "Tipo: Documento" in client.responses.prompt
    assert "Etiquetas: contrato, proveedor" in client.responses.prompt
    assert "Cláusula principal" in client.responses.prompt
    assert "Texto extraído de adjunto" in client.responses.prompt


def test_generate_knowledge_summary_reports_missing_ai_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_config_error():
        raise RuntimeError("No se encontró la clave de OpenAI")

    monkeypatch.setattr(service, "build_openai_client", _raise_config_error)

    with pytest.raises(KnowledgeSummaryConfigError, match="No hay configuración IA disponible"):
        generate_knowledge_summary({"id": 1, "title": "Nota"})


def test_custom_profile_requires_instructions() -> None:
    with pytest.raises(ValueError, match="Escribe instrucciones"):
        generate_knowledge_summary({"title": "Nota"}, summary_type="custom")


def test_generated_summary_removes_markdown_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    client.responses.create = lambda **_kwargs: type("Response", (), {"output_text": "### **Conclusión**\nDato"})()
    monkeypatch.setattr(service, "build_openai_client", lambda: client)

    assert generate_knowledge_summary({"title": "Nota", "content": "Dato"}, summary_type="key_points") == "Conclusión\nDato"


def test_source_hash_ignores_existing_summary_but_detects_content_change() -> None:
    before = {"title": "Reunión", "content": "10 kg", "summary": "", "indexed_text": "Reunión\n10 kg"}
    after_summary = {**before, "summary": "Resultado", "indexed_text": "Reunión\n10 kg\nResultado"}
    changed = {**after_summary, "content": "12 kg", "indexed_text": "Reunión\n12 kg\nResultado"}

    assert calculate_summary_source_hash(before) == calculate_summary_source_hash(after_summary)
    assert calculate_summary_source_hash(changed) != calculate_summary_source_hash(after_summary)
