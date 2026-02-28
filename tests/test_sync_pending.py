import unittest
from unittest.mock import patch

from app.core.models import AppSettings, NoteCreateRequest, NoteStatus
from app.core.service import NoteService
from app.persistence.db import Database
from app.persistence.masters_repository import MastersRepository
from app.persistence.repositories import ActionsRepository, NoteRepository, SettingsRepository


class _FakeNotionClient:
    created_tasks = []

    def __init__(self, _token: str):
        pass

    def validate_database_schema(self, _database_id: str, _settings: AppSettings):
        class _Schema:
            ok = True
            message = "ok"

        return _Schema()

    def create_page(self, _database_id: str, _settings: AppSettings, note):
        return f"page_{note.id}"

    def create_task_from_action(self, _settings: AppSettings, action_text: str, note):
        self.__class__.created_tasks.append((note.id, action_text))
        return f"task_{note.id}"


class _FakeNotionClientWithTaskError(_FakeNotionClient):
    def create_task_from_action(self, _settings: AppSettings, _action_text: str, _note):
        raise RuntimeError("boom")


class SyncPendingTaskCreationTests(unittest.TestCase):
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
        _FakeNotionClient.created_tasks = []

        self.service.save_settings(
            AppSettings(
                notion_token="token",
                notion_database_id="db",
            )
        )

    def tearDown(self):
        self.conn.close()

    def _create_note(self, acciones: str) -> int:
        return self.service.note_repo.create_note(
            NoteCreateRequest(
                title="Nota",
                raw_text="Texto",
                source="manual",
                area="Operaciones",
                tipo="Nota",
                estado="Pendiente",
                prioridad="Media",
                fecha="2025-01-01",
                resumen="",
                acciones=acciones,
            ),
            source_id=f"src-{acciones}",
            created_at=AppSettings.now_iso(),
            status=NoteStatus.PENDING,
        )

    @patch("app.core.service.NotionClient", _FakeNotionClient)
    def test_sync_pending_creates_task_per_action(self):
        note_id = self._create_note("Acción 1\n\nAcción 2\n")

        sent, failed = self.service.sync_pending()

        self.assertEqual((sent, failed), (1, 0))
        synced = self.service.note_repo.get_note(note_id)
        self.assertEqual(synced.status, "enviado")
        self.assertEqual(_FakeNotionClient.created_tasks, [(note_id, "Acción 1"), (note_id, "Acción 2")])

    @patch("app.core.service.NotionClient", _FakeNotionClientWithTaskError)
    def test_sync_pending_does_not_fail_when_task_creation_fails(self):
        note_id = self._create_note("Acción 1")

        sent, failed = self.service.sync_pending()

        self.assertEqual((sent, failed), (1, 0))
        synced = self.service.note_repo.get_note(note_id)
        self.assertEqual(synced.status, "enviado")


class DatabasePathHelper:
    @staticmethod
    def path():
        from pathlib import Path

        temp = Path("tests") / "tmp_sync_pending.db"
        if temp.exists():
            temp.unlink()
        return temp


if __name__ == "__main__":
    unittest.main()
