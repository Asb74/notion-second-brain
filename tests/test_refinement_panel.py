from app.ui.refinement_panel import (
    EMAIL_RESPONSE_PARAGRAPH_RULE,
    OUTPUT_FORMAT_PARAGRAPH,
    OUTPUT_FORMAT_TABLE,
    RefinamientoPanel,
    REFINEMENT_MODE_ATTACHMENT_SUMMARY,
    REFINEMENT_MODE_RESPONSE,
    REFINEMENT_MODE_EMAIL_SUMMARY,
    build_refinement_prompt,
    detect_format,
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


def test_build_refinement_prompt_forces_paragraph_rule_for_email_response() -> None:
    prompt = build_refinement_prompt(
        base_text="Respuesta actual",
        refinements=["formato tabla", "más formal"],
        refinement_mode=REFINEMENT_MODE_RESPONSE,
        original_context="Contexto",
        output_format=OUTPUT_FORMAT_TABLE,
    )

    assert "No utilices tablas ni listas." in prompt
    assert EMAIL_RESPONSE_PARAGRAPH_RULE in prompt
    assert "Devuelve el resultado en formato tabla." not in prompt


def test_detect_format_returns_table_for_pipe_multiline_content() -> None:
    assert detect_format("Col A | Col B\nvalor 1 | valor 2") == "table"


def test_detect_format_returns_bullets_for_dash_content() -> None:
    assert detect_format("- punto uno\n- punto dos") == "bullets"


def test_detect_format_returns_numbered_for_ordered_content() -> None:
    assert detect_format("1. punto uno\n2. punto dos") == "numbered"


def test_detect_format_returns_paragraph_for_plain_text() -> None:
    assert detect_format("Este es un correo en párrafo.") == "paragraph"


def test_sync_output_format_with_content_keeps_selected_format_for_summaries() -> None:
    panel = RefinamientoPanel.__new__(RefinamientoPanel)
    panel.refinement_mode = REFINEMENT_MODE_ATTACHMENT_SUMMARY
    panel.output_format = OUTPUT_FORMAT_PARAGRAPH
    panel.formato_seleccionado = "bullets"

    synced_format = panel.sync_output_format_with_content("Texto cualquiera")

    assert synced_format == "bullets"
    assert panel.output_format == "bullets"
    assert panel.formato_seleccionado == "bullets"


def test_sync_output_format_with_content_forces_paragraph_for_response_mode() -> None:
    panel = RefinamientoPanel.__new__(RefinamientoPanel)
    panel.refinement_mode = REFINEMENT_MODE_RESPONSE
    panel.output_format = "table"
    panel.formato_seleccionado = "table"
    panel.requested_output_format = "table"

    synced_format = panel.sync_output_format_with_content("1. Punto")

    assert synced_format == OUTPUT_FORMAT_PARAGRAPH
    assert panel.output_format == OUTPUT_FORMAT_PARAGRAPH
    assert panel.formato_seleccionado == OUTPUT_FORMAT_PARAGRAPH
