import unittest
from unittest.mock import MagicMock, patch

from app.core.models import AppSettings
from app.core.service import NoteService
from app.persistence.db import Database
from app.persistence.masters_repository import MastersRepository
from app.persistence.repositories import ActionsRepository, NoteRepository, SettingsRepository


class MastersGovernanceTests(unittest.TestCase):
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

    def test_estado_defaults_are_system_locked(self):
        rows = self.service.list_masters("Estado")
        self.assertEqual({row["value"] for row in rows}, {"Pendiente", "En curso", "Finalizado"})
        self.assertTrue(all(int(row["system_locked"]) == 1 for row in rows))

    @patch("app.core.service.NotionClient")
    def test_deactivate_master_is_blocked_when_notion_has_open_pages(self, mock_notion_client):
        self.service.save_settings(AppSettings(notion_token="token", notion_database_id="db"))
        mock_client = MagicMock()
        mock_client.count_open_pages_for_master.return_value = 2
        mock_notion_client.return_value = mock_client

        with self.assertRaises(ValueError):
            self.service.deactivate_master("Area", "General")

    @patch("app.core.service.NotionClient")
    def test_sync_schema_uses_active_values_and_skips_estado(self, mock_notion_client):
        self.service.add_master("Area", "Ventas")
        self.service.save_settings(AppSettings(notion_token="token", notion_database_id="db"))

        mock_client = MagicMock()
        mock_client.get_database_schema.return_value = {
            "properties": {
                "Area": {"select": {"options": [{"name": "General", "color": "green"}]}},
                "Tipo": {"select": {"options": []}},
                "Prioridad": {"select": {"options": []}},
                "Origen": {"select": {"options": []}},
                "Estado": {"select": {"options": [{"name": "Pendiente", "color": "blue"}]}},
            }
        }
        mock_notion_client.return_value = mock_client

        self.service.sync_schema_with_notion()

        payload = mock_client.patch_database_properties.call_args.args[1]
        self.assertIn("Area", payload)
        self.assertIn("Tipo", payload)
        self.assertIn("Prioridad", payload)
        self.assertIn("Origen", payload)
        self.assertNotIn("Estado", payload)


class DatabasePathHelper:
    @staticmethod
    def path():
        from pathlib import Path

        temp = Path("tests") / "tmp_masters_test.db"
        if temp.exists():
            temp.unlink()
        return temp


if __name__ == "__main__":
    unittest.main()
