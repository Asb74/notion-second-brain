from app.ui.refinement_panel import (
    REFINEMENT_MODE_EMAIL_SUMMARY,
    build_refinement_prompt,
)


def test_build_refinement_prompt_adds_markdown_table_instruction() -> None:
    prompt = build_refinement_prompt(
        base_text="Resumen actual",
        refinements=["formato tabla", "más breve"],
        refinement_mode=REFINEMENT_MODE_EMAIL_SUMMARY,
        original_context="Contexto",
    )

    assert "Devuelve el resultado en formato tabla Markdown con encabezados claros." in prompt
