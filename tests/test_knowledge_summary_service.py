import pytest

from app.services import knowledge_summary_service as service
from app.services.knowledge_summary_service import (
    KnowledgeSummaryConfigError,
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
