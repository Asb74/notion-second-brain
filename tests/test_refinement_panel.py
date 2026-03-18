from app.ui.refinement_panel import (
    OUTPUT_FORMAT_PARAGRAPH,
    OUTPUT_FORMAT_TABLE,
    REFINEMENT_MODE_EMAIL_SUMMARY,
    build_refinement_prompt,
)


def test_build_refinement_prompt_adds_mandatory_table_format_instruction() -> None:
    prompt = build_refinement_prompt(
        base_text="Resumen actual",
        refinements=["formato tabla", "más breve"],
        refinement_mode=REFINEMENT_MODE_EMAIL_SUMMARY,
        original_context="Contexto",
        output_format=OUTPUT_FORMAT_TABLE,
    )

    assert "FORMATO DE SALIDA (OBLIGATORIO):" in prompt
    assert "Devuelve el resultado en formato tabla." in prompt


def test_build_refinement_prompt_uses_paragraph_format_instruction_by_default() -> None:
    prompt = build_refinement_prompt(
        base_text="Resumen actual",
        refinements=["más detallado"],
        refinement_mode=REFINEMENT_MODE_EMAIL_SUMMARY,
        original_context="Contexto",
        output_format=OUTPUT_FORMAT_PARAGRAPH,
    )

    assert "No utilices tablas ni listas." in prompt
