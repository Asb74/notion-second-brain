import unittest
from unittest.mock import patch
from datetime import datetime

from app.core.hashing import compute_source_id
from app.core.models import NoteCreateRequest
from app.core.normalizer import normalize_text
from app.core.processor import ProcessedNote
from app.core.service import NoteService
from app.persistence.db import Database
from app.persistence.masters_repository import MastersRepository
from app.persistence.repositories import ActionsRepository, NoteRepository, SettingsRepository


class NormalizerTests(unittest.TestCase):
    def test_normalize_basic_whitespace(self):
        text = "  Hola\r\n\r\n   mundo\t\t test  "
        self.assertEqual(normalize_text(text, "manual"), "Hola\n\nmundo test")

    def test_email_signature_is_removed_conservatively(self):
        text = "Asunto: X\nRemitente: Y\nContenido\n\nSaludos\nJuan"
        self.assertEqual(normalize_text(text, "email_pasted"), "Asunto: X\nRemitente: Y\nContenido")


class HashTests(unittest.TestCase):
    def test_hash_is_stable(self):
        self.assertEqual(
            compute_source_id("abc", "manual"),
            compute_source_id("abc", "manual"),
        )


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(DatabasePathHelper.path())
        self.db.migrate()
        self.conn = self.db.connect()
        self.service = NoteService(
            NoteRepository(self.conn),
            SettingsRepository(self.conn),
            MastersRepository(self.conn),
            ActionsRepository(self.conn),
        )

    def tearDown(self):
        self.conn.close()

    @patch("app.core.service.process_text", return_value=ProcessedNote("", [], "", ""))
    def test_duplicate_note_is_blocked(self, _mock_process_text):
        req = NoteCreateRequest(
            title="",
            raw_text="Texto duplicado",
            source="manual",
            area="A",
            tipo="T",
            estado="Pendiente",
            prioridad="Media",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        self.assertIsNotNone(note_id)

        note_id_2, msg = self.service.create_note(req)
        self.assertIsNone(note_id_2)
        self.assertIn("duplicada", msg.lower())

    @patch("app.core.service.process_text")
    def test_create_note_applies_ai_enrichment(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Acción 1", "Acción 2"],
            tipo_sugerido="Incidencia",
            prioridad_sugerida="Alta",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con tarea",
            source="manual",
            area="A",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        note = self.service.note_repo.get_note(note_id)

        self.assertEqual(note.resumen, "Resumen AI")
        self.assertEqual(note.acciones, "")
        self.assertEqual(note.tipo, "Incidencia")
        self.assertEqual(note.prioridad, "Alta")


    @patch("app.core.service.process_text")
    def test_create_note_creates_pending_actions(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Tarea 1", "Tarea 2"],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con tareas",
            source="manual",
            area="Operaciones",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        actions = self.service.actions_repo.get_actions_by_note(note_id)

        self.assertEqual(len(actions), 2)
        self.assertTrue(all(action.status == "pendiente" for action in actions))


    @patch("app.core.service.process_text")
    def test_note_without_actions_starts_as_finalizado(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen",
            acciones=[],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto informativo",
            source="manual",
            area="A",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )

        note_id, _ = self.service.create_note(req)
        note = self.service.note_repo.get_note(note_id)

        self.assertEqual(note.estado, "Finalizado")

    @patch("app.core.service.process_text")
    def test_mark_action_done_updates_status(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Tarea única"],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con una tarea",
            source="manual",
            area="Ventas",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        action = self.service.actions_repo.get_actions_by_note(note_id)[0]

        self.service.mark_action_done(action.id)

        updated = self.service.actions_repo.get_actions_by_note(note_id)[0]
        self.assertEqual(updated.status, "hecha")
        self.assertIsNotNone(updated.completed_at)
        note = self.service.note_repo.get_note(note_id)
        self.assertEqual(note.estado, "Finalizado")


    @patch("app.core.service.NotionClient")
    @patch("app.core.service.process_text")
    def test_mark_action_done_syncs_notion_when_page_is_available(self, mock_process_text, mock_notion_client):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Tarea Notion"],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con una tarea",
            source="manual",
            area="Ventas",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        self.service.settings_repo.set_setting("notion_token", "token")
        self.service.note_repo.mark_sent(note_id, "notion-page-123")
        action = self.service.actions_repo.get_actions_by_note(note_id)[0]
        self.service.actions_repo.set_notion_page_id(action.id, "task-page-1")
        mock_notion_client.return_value.count_open_tasks_by_fuente_id.return_value = 1

        self.service.mark_action_done(action.id)

        mock_notion_client.return_value.update_page_status.assert_called_once_with(
            "task-page-1",
            "Finalizado",
            "Estado",
        )

    @patch("app.core.service.NotionClient")
    @patch("app.core.service.process_text")
    def test_mark_action_done_logs_notion_errors_without_breaking(self, mock_process_text, mock_notion_client):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Tarea Notion"],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        mock_notion_client.return_value.update_page_status.side_effect = RuntimeError("boom")
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con una tarea",
            source="manual",
            area="Ventas",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        self.service.settings_repo.set_setting("notion_token", "token")
        self.service.note_repo.mark_sent(note_id, "notion-page-123")
        action = self.service.actions_repo.get_actions_by_note(note_id)[0]
        self.service.actions_repo.set_notion_page_id(action.id, "task-page-1")

        self.service.mark_action_done(action.id)

        updated = self.service.actions_repo.get_actions_by_note(note_id)[0]
        self.assertEqual(updated.status, "hecha")

    @patch("app.core.service.NotionClient")
    @patch("app.core.service.process_text")
    def test_mark_action_done_closes_parent_note_when_all_tasks_are_done(self, mock_process_text, mock_notion_client):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Tarea Notion"],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con una tarea",
            source="manual",
            area="Ventas",
            tipo="",
            estado="Pendiente",
            prioridad="",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        self.service.settings_repo.set_setting("notion_token", "token")
        self.service.settings_repo.set_setting("notion_database_id", "db")
        self.service.note_repo.mark_sent(note_id, "notion-page-123")
        action = self.service.actions_repo.get_actions_by_note(note_id)[0]
        self.service.actions_repo.set_notion_page_id(action.id, "task-page-1")
        mock_notion_client.return_value.count_open_tasks_by_fuente_id.return_value = 0

        self.service.mark_action_done(action.id)

        self.assertEqual(mock_notion_client.return_value.update_page_status.call_count, 2)

    @patch("app.core.service.process_text")
    def test_mark_note_done_blocks_when_has_pending_tasks(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=["Tarea pendiente"],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con tarea",
            source="manual",
            area="A",
            tipo="Nota",
            estado="Pendiente",
            prioridad="Media",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)

        with self.assertRaisesRegex(ValueError, "tareas pendientes"):
            self.service.mark_note_done(note_id)

    @patch("app.core.service.NotionClient")
    @patch("app.core.service.process_text")
    def test_mark_note_done_updates_local_and_notion(self, mock_process_text, mock_notion_client):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=[],
            tipo_sugerido="Nota",
            prioridad_sugerida="Media",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto sin tareas",
            source="manual",
            area="A",
            tipo="Nota",
            estado="Pendiente",
            prioridad="Media",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        self.service.settings_repo.set_setting("notion_token", "token")
        self.service.note_repo.mark_sent(note_id, "notion-page-123")

        self.service.mark_note_done(note_id)

        note = self.service.note_repo.get_note(note_id)
        self.assertEqual(note.estado, "Finalizado")
        mock_notion_client.return_value.update_page_status.assert_called_once_with(
            "notion-page-123",
            "Finalizado",
            "Estado",
        )

    @patch("app.core.service.process_text")
    def test_create_note_keeps_manual_tipo_prioridad(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones=[],
            tipo_sugerido="Incidencia",
            prioridad_sugerida="Alta",
        )
        req = NoteCreateRequest(
            title="",
            raw_text="Texto con tarea",
            source="manual",
            area="A",
            tipo="Nota",
            estado="Pendiente",
            prioridad="Media",
            fecha=datetime.now().date().isoformat(),
        )
        note_id, _ = self.service.create_note(req)
        note = self.service.note_repo.get_note(note_id)

        self.assertEqual(note.tipo, "Nota")
        self.assertEqual(note.prioridad, "Media")


class DatabasePathHelper:
    @staticmethod
    def path():
        from pathlib import Path

        temp = Path("tests") / "tmp_test.db"
        if temp.exists():
            temp.unlink()
        return temp


if __name__ == "__main__":
    unittest.main()
