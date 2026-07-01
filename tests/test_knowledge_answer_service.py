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


def test_answer_question_from_federated_results_uses_knowledge_and_email_context(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr(service, "build_openai_client", lambda: client)

    payload = service.answer_question_from_federated_results(
        "¿Qué sé de Mercadona?",
        [
            {
                "source": "knowledge",
                "note_id": 7,
                "title": "Compra semanal",
                "area": "Archivo",
                "topic": "Viajes",
                "type": "Nota",
                "tags": "mercadona",
                "snippet": "Nota sobre Mercadona.",
                "content": "Comprar agua en Mercadona.",
                "score": 9.5,
            },
            {
                "source": "email",
                "id": "abc123",
                "title": "Ticket Mercadona",
                "subtitle": "tickets@mercadona.es",
                "date": "2026-06-30",
                "snippet": "Compra realizada en Mercadona.",
                "score": 8.0,
                "raw": {
                    "gmail_id": "abc123",
                    "subject": "Ticket Mercadona",
                    "real_sender": "tickets@mercadona.es",
                    "original_to": "yo@example.com",
                    "received_at": "2026-06-30",
                    "body_text": "Total compra Mercadona 24 euros.",
                    "attachments_json": '[{"filename":"ticket.pdf"}]',
                },
            },
        ],
    )

    assert "Usa huevos" in payload["answer"]
    assert len(payload["sources"]["knowledge"]) == 1
    assert len(payload["sources"]["emails"]) == 1
    assert "resultados locales federados" in client.responses.prompt
    assert "[Nota 1]" in client.responses.prompt
    assert "Comprar agua en Mercadona" in client.responses.prompt
    assert "[Email 1]" in client.responses.prompt
    assert "Total compra Mercadona 24 euros" in client.responses.prompt
    assert "ticket.pdf" in client.responses.prompt
    assert "Knowledge:\n- Archivo > Viajes > Compra semanal" in client.responses.prompt
    assert "Emails:\n- Asunto: Ticket Mercadona | Remitente: tickets@mercadona.es | Fecha: 2026-06-30" in client.responses.prompt


def test_answer_question_from_federated_results_reports_missing_ai_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_config_error():
        raise RuntimeError("No se encontró la clave de OpenAI")

    monkeypatch.setattr(service, "build_openai_client", _raise_config_error)

    with pytest.raises(KnowledgeAnswerConfigError, match="No hay configuración IA disponible"):
        service.answer_question_from_federated_results(
            "¿Qué emails tengo?",
            [{"source": "email", "title": "Aviso", "snippet": "texto", "score": 1}],
        )
