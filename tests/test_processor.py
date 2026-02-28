import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.processor import SYSTEM_PROMPT, process_text


class ProcessorTests(unittest.TestCase):
    def test_system_prompt_includes_action_extraction_requirements(self):
        self.assertIn("Extraer acciones explícitas e implícitas", SYSTEM_PROMPT)
        self.assertIn("ha de ser", SYSTEM_PROMPT)
        self.assertIn("No omitas tareas implícitas", SYSTEM_PROMPT)
        self.assertIn("Cada acción debe ser independiente", SYSTEM_PROMPT)
        self.assertIn("Definir y ejecutar la acción requerida", SYSTEM_PROMPT)
        self.assertIn("Si no hay acciones reales, devuelve lista vacía", SYSTEM_PROMPT)

    @patch("app.core.processor.build_openai_client")
    def test_acciones_stringified_list_is_normalized(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    output_text="""{"resumen": "R", "acciones": "[' A1 ', '', 'A2']", "tipo_sugerido": "Nota", "prioridad_sugerida": "Media"}"""
                )
            )
        )

        processed = process_text("texto")

        self.assertEqual(processed.acciones, ["A1", "A2"])

    @patch("app.core.processor.build_openai_client")
    def test_acciones_invalid_string_falls_back_to_single_item(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    output_text='{"resumen": "R", "acciones": "Acción sin formato"}'
                )
            )
        )

        processed = process_text("texto")

        self.assertEqual(processed.acciones, ["Acción sin formato"])

    @patch("app.core.processor.build_openai_client")
    def test_acciones_non_list_is_wrapped(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    output_text='{"resumen": "R", "acciones": 42}'
                )
            )
        )

        processed = process_text("texto")

        self.assertEqual(processed.acciones, ["42"])

    @patch("app.core.processor.build_openai_client")
    def test_acciones_multiline_are_split_as_individual_actions(self, mock_build_client):
        mock_build_client.return_value = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    output_text='{"resumen": "R", "acciones": ["- A1\\n- A2"]}'
                )
            )
        )

        processed = process_text("texto")

        self.assertEqual(processed.acciones, ["A1", "A2"])


if __name__ == "__main__":
    unittest.main()
