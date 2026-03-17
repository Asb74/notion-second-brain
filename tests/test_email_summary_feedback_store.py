import json

from app.ml.email_summary_feedback_store import EmailSummaryFeedbackStore
from app.ml.global_learning_store import MAX_RECORDS_PER_TYPE, GlobalLearningStore


def test_guardar_feedback_persists_samples(tmp_path) -> None:
    path = tmp_path / "training_data.json"
    store = EmailSummaryFeedbackStore(path)

    result = store.guardar_feedback(
        email="EMAIL_BODY:\nPedido 123",
        ai_output="• Pedido en revisión",
        user_final="• Pedido 123 en revisión\n• Cliente ACME espera confirmación",
        instrucciones="Incluye cliente",
    )

    assert result["saved"] is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["email_summary"]) == 1
    assert payload["email_summary"][0]["instructions"] == "Incluye cliente"


def test_guardar_feedback_rejects_empty_or_short(tmp_path) -> None:
    store = EmailSummaryFeedbackStore(tmp_path / "training_data.json")

    empty_result = store.guardar_feedback(email="", ai_output="a", user_final="b", instrucciones="")
    short_result = store.guardar_feedback(
        email="EMAIL_BODY: hola",
        ai_output="Resumen",
        user_final="corto",
        instrucciones="",
    )

    assert empty_result == {"saved": False, "reason": "empty"}
    assert short_result == {"saved": False, "reason": "too_short"}


def test_build_prompt_context_returns_last_n_examples(tmp_path) -> None:
    store = EmailSummaryFeedbackStore(tmp_path / "training_data.json")
    for idx in range(1, 4):
        store.guardar_feedback(
            email=f"EMAIL_BODY:\nContenido {idx}",
            ai_output=f"Resumen IA {idx}",
            user_final=f"Resumen final correcto {idx}",
            instrucciones=f"Instrucción {idx}",
        )

    context = store.build_prompt_context(limit=2)

    assert "Contenido 1" not in context
    assert "Contenido 2" in context
    assert "Contenido 3" in context
    assert "RESPUESTA CORRECTA" in context


def test_global_learning_store_limits_and_deduplicates(tmp_path) -> None:
    store = GlobalLearningStore(tmp_path / "training_data.json")
    first = store.guardar_feedback(
        tipo="email_reply",
        input_text="**hola**",
        ai_output="respuesta",
        user_final="respuesta final larga",
        instrucciones="",
    )
    duplicate = store.guardar_feedback(
        tipo="email_reply",
        input_text="hola",
        ai_output="respuesta",
        user_final="respuesta final larga",
        instrucciones="",
    )

    assert first["saved"] is True
    assert duplicate == {"saved": False, "reason": "duplicate"}

    for idx in range(MAX_RECORDS_PER_TYPE + 10):
        store.guardar_feedback(
            tipo="email_reply",
            input_text=f"mail {idx}",
            ai_output="respuesta",
            user_final=f"respuesta final válida {idx}",
            instrucciones="",
        )
    data = json.loads((tmp_path / "training_data.json").read_text(encoding="utf-8"))
    assert len(data["email_reply"]) == MAX_RECORDS_PER_TYPE
