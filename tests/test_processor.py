import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.processor import SYSTEM_PROMPT, process_text


class ProcessorTests(unittest.TestCase):
    def test_system_prompt_replaced_with_new_template(self):
        self.assertEqual(SYSTEM_PROMPT, "[PEGA AQUÍ EL PROMPT NUEVO COMPLETO]")

    @patch("app.core.processor.build_openai_client")
    def test_acciones_non_list_is_wrapped(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(output_text='{"resumen": "R", "acciones": 42}')
            )
        )

        processed = process_text("texto")

        self.assertEqual(processed.acciones, ["42"])

    @patch("app.core.processor.build_openai_client")
    def test_acciones_object_reads_contexto_and_fecha_detectada(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    output_text='{"acciones": [{"descripcion": "Llamar al cliente", "contexto": ["ventas", "urgente"], "fecha_detectada": "2026-01-15"}]}'
                )
            )
        )

        processed = process_text("texto")

        self.assertEqual(
            processed.acciones,
            ["Llamar al cliente [Contexto: ventas, urgente; Fecha: 2026-01-15]"],
        )

    @patch("app.core.processor.build_openai_client")
    def test_acciones_object_ignores_null_fecha_detectada(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    output_text='{"acciones": [{"descripcion": "Enviar propuesta", "contexto": ["comercial"], "fecha_detectada": null}]}'
                )
            )
        )

        processed = process_text("texto")

        self.assertEqual(processed.acciones, ["Enviar propuesta [Contexto: comercial]"])


if __name__ == "__main__":
    unittest.main()
