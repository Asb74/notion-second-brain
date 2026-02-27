import unittest
from datetime import datetime

from app.core.hashing import compute_source_id
from app.core.models import NoteCreateRequest
from app.core.normalizer import normalize_text
from app.core.service import NoteService
from app.persistence.db import Database
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
        self.service = NoteService(NoteRepository(self.conn), SettingsRepository(self.conn))

    def tearDown(self):
        self.conn.close()

    def test_duplicate_note_is_blocked(self):
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
        self.assertIn("Duplicado", msg)


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
