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

    def get_email_attachments(self, _gmail_id: str):
        return [{"filename": "factura.pdf"}, {"filename": "albaran.pdf"}]

    def select_email_by_gmail_id(self, gmail_id: str):
        self.selected = gmail_id
        return True

    def set_response_draft(self, value: str):
        self.reply = value

    def focus_force(self):
        self.focused = True


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


def test_responder_email_prefills_reply_text() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    email_window = _EmailWindowStub()
    window.open_email_manager_callback = lambda: email_window
    window._selected_record = {"kind": "EMAIL", "id": 1, "source_id": "gid-7", "title": "Asunto", "content": "Cuerpo"}

    window.responder_email()

    assert email_window.selected == "gid-7"
    assert email_window.reply.startswith("Re: Asunto")
    assert "Mensaje original" in email_window.reply


def test_reenviar_email_prefills_forward_text() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    email_window = _EmailWindowStub()
    window.open_email_manager_callback = lambda: email_window
    window._selected_record = {"kind": "EMAIL", "id": 1, "source_id": "gid-8", "title": "Asunto", "content": "Cuerpo"}

    window.reenviar_email()

    assert email_window.selected == "gid-8"
    assert email_window.reply.startswith("Fwd: Asunto")


def test_abrir_email_uses_gmail_url_when_id_exists() -> None:
    window = CalendarManagerWindow.__new__(CalendarManagerWindow)
    window._selected_record = {"kind": "EMAIL", "source_id": "gid-9"}
    with patch("app.ui.calendar_manager_window.webbrowser.open") as opener:
        window.abrir_email()
    opener.assert_called_once_with("https://mail.google.com/mail/u/0/#inbox/gid-9")
