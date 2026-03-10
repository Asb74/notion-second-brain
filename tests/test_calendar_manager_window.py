import sys
import types
from unittest.mock import patch

# Stubs to import UI module without optional deps.
google = types.ModuleType("google")
auth = types.ModuleType("google.auth")
transport = types.ModuleType("google.auth.transport")
requests = types.ModuleType("google.auth.transport.requests")
requests.Request = object
oauth2 = types.ModuleType("google.oauth2")
credentials = types.ModuleType("google.oauth2.credentials")
credentials.Credentials = object
oauthlib = types.ModuleType("google_auth_oauthlib")
flow = types.ModuleType("google_auth_oauthlib.flow")
flow.InstalledAppFlow = object
apiclient = types.ModuleType("googleapiclient")
discovery = types.ModuleType("googleapiclient.discovery")
discovery.build = lambda *args, **kwargs: None

sys.modules.setdefault("google", google)
sys.modules.setdefault("google.auth", auth)
sys.modules.setdefault("google.auth.transport", transport)
sys.modules.setdefault("google.auth.transport.requests", requests)
sys.modules.setdefault("google.oauth2", oauth2)
sys.modules.setdefault("google.oauth2.credentials", credentials)
sys.modules.setdefault("google_auth_oauthlib", oauthlib)
sys.modules.setdefault("google_auth_oauthlib.flow", flow)
sys.modules.setdefault("googleapiclient", apiclient)
sys.modules.setdefault("googleapiclient.discovery", discovery)

from app.ui.calendar_manager_window import CalendarManagerWindow


class _ListboxStub:
    def __init__(self) -> None:
        self.items: list[str] = []

    def delete(self, *_args) -> None:
        self.items = []

    def insert(self, _index: str, value: str) -> None:
        self.items.append(value)


class _EmailWindowStub:
    def __init__(self) -> None:
        self.selected = None
        self.reply = None
        self.focused = False
        self.reply_called = False
        self.forward_called = False

    def get_email_attachments(self, _gmail_id: str):
        return [{"filename": "factura.pdf"}, {"filename": "albaran.pdf"}]

    def select_email_by_gmail_id(self, gmail_id: str):
        self.selected = gmail_id
        return True

    def set_response_draft(self, value: str):
        self.reply = value

    def focus_force(self):
        self.focused = True

    def _create_outlook_draft(self):
        self.reply_called = True

    def _forward_email(self):
        self.forward_called = True


def test_resolve_gmail_id_prefers_source_id() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    item = {"source_id": "abc123", "email_link": "https://mail.google.com/mail/u/0/#all/zzz"}
    assert window._resolve_gmail_id(item) == "abc123"


def test_render_email_attachments_lists_files() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    window._attachments_by_name = {}
    window.attachments_list = _ListboxStub()
    email_window = _EmailWindowStub()
    window.open_email_manager_callback = lambda: email_window

    window._render_email_attachments({"kind": "EMAIL", "source_id": "gid-1"})

    assert "📄 factura.pdf" in window.attachments_list.items
    assert "📄 albaran.pdf" in window.attachments_list.items


def test_prompt_email_response_uses_completion_callback() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    calls = []
    window.email_completion_callback = lambda payload: calls.append(payload)

    window._prompt_email_response({"source_id": "gid-2", "content": "texto"})

    assert calls == [{"gmail_id": "gid-2", "thread_id": "", "to": "", "subject": "Re: ", "body": "texto"}]


def test_responder_email_uses_email_manager_reply_action() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    email_window = _EmailWindowStub()
    window.open_email_manager_callback = lambda: email_window
    window._selected_record = {"kind": "EMAIL", "id": 1, "source_id": "gid-7", "title": "Asunto", "content": "Cuerpo"}

    window.responder_email()

    assert email_window.selected == "gid-7"
    assert email_window.reply_called is True


def test_reenviar_email_uses_email_manager_forward_action() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    email_window = _EmailWindowStub()
    window.open_email_manager_callback = lambda: email_window
    window._selected_record = {"kind": "EMAIL", "id": 1, "source_id": "gid-8", "title": "Asunto", "content": "Cuerpo"}

    window.reenviar_email()

    assert email_window.selected == "gid-8"
    assert email_window.forward_called is True


def test_abrir_email_selects_email_in_manager() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    email_window = _EmailWindowStub()
    window.open_email_manager_callback = lambda: email_window
    window._selected_record = {"kind": "EMAIL", "source_id": "gid-9"}

    window.abrir_email()

    assert email_window.selected == "gid-9"


def test_create_action_uses_note_id_when_id_is_missing() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    created_for_note_ids: list[int] = []

    class _ActionsRepo:
        def create_action(self, note_id: int, _description: str, _area: str):
            created_for_note_ids.append(note_id)

    class _NoteService:
        actions_repo = _ActionsRepo()

        @staticmethod
        def get_note_by_id(note_id: int):
            class _Note:
                area = "general"

            return _Note() if note_id == 42 else None

    window.note_service = _NoteService()
    window.content_text = type("TextStub", (), {"get": lambda *_args: "Nueva acción"})()
    window.refresh_calendar_view = lambda: None
    window._selected_record = {"kind": "EVENT", "note_id": 42}

    window._create_action_for_current_note()

    assert created_for_note_ids == [42]


def test_save_inline_action_ignores_empty_value() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)

    class _EntryStub:
        @staticmethod
        def get() -> str:
            return "   "

    class _RowStub:
        def __init__(self) -> None:
            self.destroyed = False

        def destroy(self) -> None:
            self.destroyed = True

    window._inline_action_entry = _EntryStub()
    window._inline_action_row = _RowStub()
    window._inline_action_saving = False

    result = window._save_inline_action()

    assert result == "break"
    assert window._inline_action_row is None
    assert window._inline_action_entry is None


def test_save_inline_action_creates_action_and_rerenders() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    calls: list[tuple[int, str, str]] = []
    rerendered: list[dict[str, str | int]] = []

    class _EntryStub:
        @staticmethod
        def get() -> str:
            return "  Nueva tarea inline  "

    class _RowStub:
        @staticmethod
        def destroy() -> None:
            return None

    class _ActionsRepo:
        @staticmethod
        def create_action(note_id: int, description: str, area: str) -> None:
            calls.append((note_id, description, area))

    class _NoteService:
        actions_repo = _ActionsRepo()

        @staticmethod
        def get_note_by_id(note_id: int):
            class _Note:
                area = "general"

            return _Note() if note_id == 42 else None

    window._inline_action_entry = _EntryStub()
    window._inline_action_row = _RowStub()
    window._inline_action_saving = False
    window._selected_record = {"note_id": 42}
    window.note_service = _NoteService()
    window.refresh_calendar_view = lambda: None
    window._render_associated_actions = lambda record: rerendered.append(record)
    window._update_detail_scrollregion = lambda *_args, **_kwargs: None

    result = window._save_inline_action()

    assert result == "break"
    assert calls == [(42, "Nueva tarea inline", "general")]
    assert rerendered == [{"note_id": 42}]


def test_save_inline_title_edit_updates_note_and_refreshes() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    updates: list[tuple[int, str, str]] = []
    refreshed: list[bool] = []

    class _EntryStub:
        @staticmethod
        def get() -> str:
            return "  Nuevo título inline  "

        @staticmethod
        def destroy() -> None:
            return None

    class _TextStub:
        @staticmethod
        def get(*_args) -> str:
            return "contenido actualizado"

    class _DetailTitleVarStub:
        def __init__(self) -> None:
            self.value = "Anterior"

        def set(self, value: str) -> None:
            self.value = value

    class _NoteService:
        @staticmethod
        def update_note_title(note_id: int, title: str, content: str) -> None:
            updates.append((note_id, title, content))

    window._inline_title_entry = _EntryStub()
    window._inline_title_saving = False
    window._selected_record = {"note_id": 42, "title": "Anterior"}
    window.content_text = _TextStub()
    window.note_service = _NoteService()
    window.detail_title_var = _DetailTitleVarStub()
    window._restore_title_label = lambda: setattr(window, "_inline_title_entry", None)
    window.refresh_calendar_view = lambda: refreshed.append(True)

    result = window._save_inline_title_edit()

    assert result == "break"
    assert updates == [(42, "Nuevo título inline", "contenido actualizado")]
    assert window._selected_record["title"] == "Nuevo título inline"
    assert window.detail_title_var.value == "Nuevo título inline"
    assert refreshed == [True]


def test_start_inline_title_edit_requires_selected_note() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    window._inline_title_entry = None
    window._selected_record = None

    result = window._start_inline_title_edit()

    assert result == "break"


def test_partition_day_entries_keeps_unslotted_in_top_and_maps_arbitrary_time() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    entries = [
        {"title": "Nota sin hora", "time": "", "created_at": "2024-01-10T09:00:00"},
        {"title": "Evento exacto", "time": "08:00"},
        {"title": "Cita médica", "time": "19:22"},
    ]
    slots = ["08:00", "08:30", "19:00", "19:30"]

    timed_map, top_entries = window._partition_day_entries(entries, slots)

    assert [entry["title"] for entry in top_entries] == ["Nota sin hora"]
    assert [entry["title"] for entry in timed_map["08:00"]] == ["Evento exacto"]
    assert [entry["title"] for entry in timed_map["19:30"]] == ["Cita médica"]


def test_actions_for_day_returns_only_matching_actions() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    window.actions = [
        types.SimpleNamespace(
            id=1,
            note_id=99,
            description="Seguimiento cliente",
            status="pendiente",
            created_at="2024-02-20T10:15:00",
            completed_at=None,
        ),
        types.SimpleNamespace(
            id=2,
            note_id=100,
            description="Acción fuera de día",
            status="pendiente",
            created_at="2024-02-21T10:15:00",
            completed_at=None,
        ),
    ]

    rows = window._actions_for_day(window._safe_parse_date("2024-02-20") )

    assert len(rows) == 1
    assert rows[0]["kind"] == "ACTION"
    assert rows[0]["title"] == "Seguimiento cliente"
    assert rows[0]["time"] == ""
