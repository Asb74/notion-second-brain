from pathlib import Path

from app.core.email.gmail_client import GmailClient


def test_execute_with_reauth_retries_operation_when_token_is_invalid():
    client = GmailClient.__new__(GmailClient)
    calls = {"count": 0}
    reauth_calls = []

    def operation():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("invalid_grant")
        return {"ok": True}

    client._is_invalid_grant_error = lambda exc: "invalid_grant" in str(exc)
    client._reauthenticate_oauth = lambda: reauth_calls.append("reauth")

    result = GmailClient._execute_with_reauth(client, operation)

    assert result == {"ok": True}
    assert calls["count"] == 2
    assert reauth_calls == ["reauth"]


def test_reauthenticate_oauth_removes_old_token_and_saves_new_one(tmp_path, monkeypatch):
    token_path = tmp_path / "gmail_token.json"
    token_path.write_text("old-token", encoding="utf-8")

    client = GmailClient.__new__(GmailClient)
    client.token_path = str(token_path)

    class _Creds:
        def to_json(self):
            return '{"token":"new"}'

    monkeypatch.setattr(client, "_run_installed_app_flow", lambda: _Creds())
    monkeypatch.setattr("app.core.email.gmail_client.build", lambda *args, **kwargs: "service")

    creds = GmailClient._reauthenticate_oauth(client)

    assert isinstance(creds, _Creds)
    assert token_path.read_text(encoding="utf-8") == '{"token":"new"}'
    assert client.service == "service"


def test_reauthenticate_oauth_shows_error_message_when_flow_fails(tmp_path, monkeypatch):
    token_path = Path(tmp_path) / "gmail_token.json"

    client = GmailClient.__new__(GmailClient)
    client.token_path = str(token_path)

    monkeypatch.setattr(
        client,
        "_run_installed_app_flow",
        lambda: (_ for _ in ()).throw(RuntimeError("oauth-failed")),
    )

    error_calls = []
    monkeypatch.setattr(
        "app.core.email.gmail_client.messagebox.showerror",
        lambda title, message: error_calls.append((title, message)),
    )

    try:
        GmailClient._reauthenticate_oauth(client)
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "oauth-failed"

    assert error_calls == [
        (
            "Autenticación Gmail",
            "No se pudo renovar el acceso a Gmail. Debes autenticar nuevamente.",
        )
    ]
