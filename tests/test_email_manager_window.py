import sqlite3
import sys
import types

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

from app.ui.email_manager_window import EmailManagerWindow, is_real_html


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


def test_set_html_preview_keeps_html_for_real_html_content() -> None:
    window = EmailManagerWindow.__new__(EmailManagerWindow)
    window.preview_html = _PreviewStub()
    window._expanded_html_frame = None
    window._current_html_content = ""

    EmailManagerWindow._set_html_preview(window, "<div><p>ok</p></div>", "text")

    assert window._current_html_content == "<div><p>ok</p></div>"
    assert window.preview_html.value == "<div><p>ok</p></div>"
