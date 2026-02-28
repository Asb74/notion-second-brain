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
from app.persistence.repositories import NoteRepository, SettingsRepository


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
        )

    def tearDown(self):
        self.conn.close()

    @patch("app.core.service.process_text", return_value=ProcessedNote("", "", "", ""))
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
            acciones="- Acción 1",
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
        self.assertEqual(note.acciones, "- Acción 1")
        self.assertEqual(note.tipo, "Incidencia")
        self.assertEqual(note.prioridad, "Alta")

    @patch("app.core.service.process_text")
    def test_create_note_keeps_manual_tipo_prioridad(self, mock_process_text):
        mock_process_text.return_value = ProcessedNote(
            resumen="Resumen AI",
            acciones="",
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
