import unittest
from unittest.mock import patch

from app.core.models import AppSettings, Note
from app.integrations.notion_client import NotionClient


class _DummyResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"id": "page_123"}
        self.text = ""

    def json(self):
        return self._body


class NotionClientCreatePageTests(unittest.TestCase):
    def setUp(self):
        self.client = NotionClient("token")
        self.settings = AppSettings(notion_token="token", notion_database_id="db")

    def _build_note(self, title: str) -> Note:
        return Note(
            id=1,
            created_at="2025-01-01T00:00:00",
            source="manual",
            source_id="src",
            title=title,
            raw_text="Texto original",
            area="Área",
            tipo="Nota",
            estado="Pendiente",
            prioridad="Media",
            fecha="2025-01-01",
            resumen="",
            acciones="",
            status="pendiente",
            notion_page_id=None,
            last_error=None,
            attempts=0,
            next_retry_at=None,
        )

    @patch("requests.post")
    def test_create_page_uses_note_title_limited_to_200_chars(self, mock_post):
        mock_post.return_value = _DummyResponse()
        long_title = "A" * 250

        self.client.create_page("db", self.settings, self._build_note(long_title))

        payload = mock_post.call_args.kwargs["json"]
        content = payload["properties"][self.settings.prop_title]["title"][0]["text"]["content"]
        self.assertEqual(content, "A" * 200)

    @patch("requests.post")
    def test_create_page_uses_default_title_when_empty(self, mock_post):
        mock_post.return_value = _DummyResponse()

        self.client.create_page("db", self.settings, self._build_note("   "))

        payload = mock_post.call_args.kwargs["json"]
        content = payload["properties"][self.settings.prop_title]["title"][0]["text"]["content"]
        self.assertEqual(content, "Sin título")

    @patch("requests.post")
    def test_create_task_from_action_builds_expected_payload(self, mock_post):
        mock_post.return_value = _DummyResponse(body={"id": "task_1"})
        note = self._build_note("Nota madre")

        self.client.create_task_from_action(self.settings, "  Hacer seguimiento con cliente  ", note)

        payload = mock_post.call_args.kwargs["json"]
        properties = payload["properties"]
        self.assertEqual(payload["parent"]["database_id"], self.settings.notion_database_id)
        self.assertEqual(properties[self.settings.prop_tipo]["select"]["name"], "Tarea")
        self.assertEqual(properties[self.settings.prop_estado]["select"]["name"], "Pendiente")
        self.assertEqual(properties[self.settings.prop_area]["select"]["name"], note.area)
        self.assertEqual(properties[self.settings.prop_fecha]["date"]["start"], note.fecha)
        self.assertEqual(properties["Origen"]["select"]["name"], "Sistema")
        self.assertEqual(properties["Fuente_ID"]["rich_text"][0]["text"]["content"], str(note.source_id))
        self.assertEqual(properties["Raw"]["rich_text"][0]["text"]["content"], "Hacer seguimiento con cliente")
        self.assertEqual(properties[self.settings.prop_prioridad]["select"]["name"], note.prioridad)



    @patch("requests.patch")
    def test_update_page_status_uses_estado_property(self, mock_patch):
        mock_patch.return_value = _DummyResponse(body={"id": "page_1"})

        self.client.update_page_status("page_1", "Finalizado")

        self.assertEqual(
            mock_patch.call_args.args[0],
            "https://api.notion.com/v1/pages/page_1",
        )
        self.assertEqual(
            mock_patch.call_args.kwargs["json"],
            {
                "properties": {
                    "Estado": {
                        "select": {"name": "Finalizado"}
                    }
                }
            },
        )

if __name__ == "__main__":
    unittest.main()
