import pytest

from app.services import knowledge_answer_service as service
from app.services.knowledge_answer_service import (
    KnowledgeAnswerConfigError,
    answer_question_from_knowledge,
)


class _FakeResponses:
    def __init__(self):
        self.prompt = ""

    def create(self, model: str, input: str):  # noqa: A002 - mirrors OpenAI client API
        self.prompt = input
        return type("Response", (), {"output_text": "Respuesta:\nUsa huevos.\n\nFuentes consultadas:\n- Cocina > Recetas > Tortilla"})()


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()


def test_answer_question_from_knowledge_uses_only_local_result_context(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr(service, "build_openai_client", lambda: client)

    payload = answer_question_from_knowledge(
        "¿Qué recetas tengo que lleven huevo?",
        [
            {
                "note_id": 7,
                "title": "Tortilla",
                "area": "Cocina",
                "topic": "Recetas",
                "type": "Receta",
                "tags": "huevo, cena",
                "snippet": "Receta con huevos y patatas.",
                "content": "Batir huevos con patatas y cebolla.",
                "indexed_text": "",
                "score": 9.5,
            }
        ],
    )

    assert "Usa huevos" in payload["answer"]
    assert payload["sources"] == [
        {
            "note_id": 7,
            "title": "Tortilla",
            "area": "Cocina",
            "topic": "Recetas",
            "snippet": "Receta con huevos y patatas.",
        }
    ]
    assert "No uses internet ni conocimiento externo" in client.responses.prompt
    assert "Usa solo el contexto proporcionado" in client.responses.prompt
    assert "Pregunta del usuario:" in client.responses.prompt
    assert "Batir huevos con patatas" in client.responses.prompt
    assert "Fuentes consultadas:\n- Cocina > Recetas > Tortilla" in client.responses.prompt


def test_answer_question_from_knowledge_reports_no_info_without_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unexpected_client():
        raise AssertionError("AI client should not be called without local results")

    monkeypatch.setattr(service, "build_openai_client", _unexpected_client)

    payload = answer_question_from_knowledge("¿Qué sé de algo que no existe?", [])

    assert payload == {
        "answer": "No he encontrado información suficiente en Knowledge para responder con seguridad.",
        "sources": [],
    }


def test_answer_question_from_knowledge_reports_missing_ai_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_config_error():
        raise RuntimeError("No se encontró la clave de OpenAI")

    monkeypatch.setattr(service, "build_openai_client", _raise_config_error)

    with pytest.raises(KnowledgeAnswerConfigError, match="No hay configuración IA disponible"):
        answer_question_from_knowledge(
            "¿Qué recetas tengo?",
            [{"note_id": 1, "title": "Tortilla", "snippet": "huevos", "score": 1}],
        )
