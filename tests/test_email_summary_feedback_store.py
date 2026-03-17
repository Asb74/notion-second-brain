import json

from app.ml.email_summary_feedback_store import EmailSummaryFeedbackStore


def test_guardar_feedback_persists_samples(tmp_path) -> None:
    store = EmailSummaryFeedbackStore(tmp_path / "training_data_email_summary.json")

    result = store.guardar_feedback(
        email="EMAIL_BODY:\nPedido 123",
        ai_output="• Pedido en revisión",
        user_final="• Pedido 123 en revisión\n• Cliente ACME espera confirmación",
        instrucciones="Incluye cliente",
    )

    assert result["saved"] is True
    payload = json.loads((tmp_path / "training_data_email_summary.json").read_text(encoding="utf-8"))
    assert len(payload["samples"]) == 1
    assert payload["samples"][0]["refinement_instructions"] == "Incluye cliente"


def test_guardar_feedback_rejects_empty_or_short(tmp_path) -> None:
    store = EmailSummaryFeedbackStore(tmp_path / "training_data_email_summary.json")

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
    store = EmailSummaryFeedbackStore(tmp_path / "training_data_email_summary.json")
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
    assert "Resumen correcto" in context
