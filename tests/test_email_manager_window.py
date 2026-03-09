import sqlite3
import sys
import types
from unittest.mock import patch

# Minimal stubs so importing UI module does not require optional google deps.
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
errors = types.ModuleType("googleapiclient.errors")
errors.HttpError = Exception

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
sys.modules.setdefault("googleapiclient.errors", errors)

from app.ui.email_manager_window import EmailManagerWindow, clean_outlook_styles, is_real_html


class _PreviewStub:
    def __init__(self) -> None:
        self.value = ""

    def set_html(self, value: str) -> None:
        self.value = value


def test_create_notes_no_row_get() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE emails (
            gmail_id TEXT PRIMARY KEY,
            subject TEXT,
            sender TEXT,
            real_sender TEXT,
            original_from TEXT,
            received_at TEXT,
            body_text TEXT,
            body_html TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, real_sender, original_from, received_at, body_text, body_html)
        VALUES ('id-1', 'Asunto', 'forwarder@example.com', 'real@example.com', 'preferred@example.com', '2024-01-01T00:00:00+00:00', 'hola', '')
        """
    )
    row = conn.execute("SELECT * FROM emails WHERE gmail_id = 'id-1'").fetchone()
    assert row is not None

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._compose_note_text = lambda subject, sender, body_text, body_html: f"{subject}|{sender}|{body_text}|{body_html}"
    window._resolve_default_value = lambda *_args: "valor"
    window._resolve_note_date = lambda _value: "2024-01-01"

    request = EmailManagerWindow._build_note_request_from_row(window, row)

    assert request.title == "Asunto"
    assert "preferred@example.com" in request.raw_text


def test_is_real_html_detects_known_html_tags() -> None:
    assert is_real_html("<html><body><p>hola</p></body></html>")
    assert is_real_html("<table><tr><td>row</td></tr></table>")
    assert not is_real_html("Normal DocumentEmail table.MsoNormalTable font-family: Times New Roman")
    assert not is_real_html("")


def test_set_html_preview_uses_text_fallback_for_non_html() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.preview_html = _PreviewStub()
    window._expanded_html_frame = None
    window._current_html_content = ""

    EmailManagerWindow._set_html_preview(window, "Normal DocumentEmail", "line 1\nline <2>")

    assert window._current_html_content == ""
    assert window.preview_html.value == "<pre>line 1\nline &lt;2&gt;</pre>"


def test_clean_outlook_styles_removes_mso_and_list_noise() -> None:
    raw = """{mso-level-number-format:bullet; mso-level-text:\\F0B7;}\n@list l1:level6 {mso-level-number-format:bullet; font-family:Wingdings;}\nfont-family: Times New Roman\nDe: Antonio Sánchez"""

    cleaned = clean_outlook_styles(raw)

    assert "mso-" not in cleaned.lower()
    assert "@list" not in cleaned.lower()
    assert "De: Antonio Sánchez" in cleaned


def test_set_html_preview_keeps_html_for_real_html_content() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.preview_html = _PreviewStub()
    window._expanded_html_frame = None
    window._current_html_content = ""

    EmailManagerWindow._set_html_preview(window, "<div><p>ok</p></div>", "text")

    assert window._current_html_content == "<div><p>ok</p></div>"
    assert window.preview_html.value == "<div><p>ok</p></div>"

class _TreeStub:
    def __init__(self) -> None:
        self.selected = ()
        self.focused = None
        self.seen = None

    def selection_set(self, ids):
        self.selected = tuple(ids)

    def focus(self, iid):
        self.focused = iid

    def see(self, iid):
        self.seen = iid

    def selection(self):
        return self.selected


class _TextStub:
    def __init__(self) -> None:
        self.value = ""

    def delete(self, *_args) -> None:
        self.value = ""

    def insert(self, _index: str, text: str) -> None:
        self.value = text


def test_select_email_by_gmail_id_selects_row_and_refreshes_preview() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.email_repo = type("Repo", (), {"get_email_content": lambda *_args: {"type": "priority"}})()
    window._rows_by_id = {"id-1": {"gmail_id": "id-1"}}
    window._tab_to_types = {"Prioridad": ["priority"]}
    window._current_tab = "Prioridad"
    window._set_tab_by_label = lambda _label: None
    window.refresh_emails = lambda: None
    refreshed = {"called": False}
    window._refresh_preview = lambda: refreshed.update(called=True)
    window.tree = _TreeStub()

    selected = EmailManagerWindow.select_email_by_gmail_id(window, "id-1")

    assert selected is True
    assert window.tree.selected == ("id-1",)
    assert window.tree.focused == "id-1"
    assert window.tree.seen == "id-1"
    assert refreshed["called"] is True


def test_set_response_draft_tracks_pending_note_id() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.response_text = _TextStub()
    tree = _TreeStub()
    tree.selection_set(("id-77",))
    window.tree = tree
    window._pending_note_id_by_gmail_id = {}

    EmailManagerWindow.set_response_draft(window, "hola", note_id=33)

    assert window.response_text.value == "hola"
    assert window._pending_note_id_by_gmail_id["id-77"] == 33


def test_select_email_by_gmail_id_warns_when_email_not_found() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.email_repo = type("Repo", (), {"get_email_content": lambda *_args: None})()
    window._rows_by_id = {}
    window._tab_to_types = {}
    window._current_tab = "Prioridad"
    window.tree = _TreeStub()

    with patch("app.ui.email_manager_window.messagebox.showwarning") as showwarning:
        selected = EmailManagerWindow.select_email_by_gmail_id(window, "missing-id")

    assert selected is False
    showwarning.assert_called_once_with("Email no encontrado", "No se encontró el correo original asociado.")


def test_set_reply_body_delegates_to_set_response_draft() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    called: dict[str, int | str | None] = {}

    def _capture(body: str, note_id: int | None = None) -> None:
        called["body"] = body
        called["note_id"] = note_id

    window.set_response_draft = _capture  # type: ignore[method-assign]
    EmailManagerWindow.set_reply_body(window, "texto de prueba", note_id=21)

    assert called == {"body": "texto de prueba", "note_id": 21}


def test_build_note_request_uses_custom_title_when_provided() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE emails (
            gmail_id TEXT PRIMARY KEY,
            subject TEXT,
            sender TEXT,
            real_sender TEXT,
            original_from TEXT,
            received_at TEXT,
            body_text TEXT,
            body_html TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, real_sender, original_from, received_at, body_text, body_html)
        VALUES ('id-2', 'Asunto original', 'sender@example.com', 'real@example.com', '', '2024-01-01T00:00:00+00:00', 'hola', '')
        """
    )
    row = conn.execute("SELECT * FROM emails WHERE gmail_id = 'id-2'").fetchone()
    assert row is not None

    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window._compose_note_text = lambda subject, sender, body_text, body_html: f"{subject}|{sender}|{body_text}|{body_html}"
    window._resolve_default_value = lambda *_args: "valor"
    window._resolve_note_date = lambda _value: "2024-01-01"

    request = EmailManagerWindow._build_note_request_from_row(window, row, "Título editable")

    assert request.title == "Título editable"
    assert request.raw_text.startswith("Título editable|")
