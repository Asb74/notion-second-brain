import sys
from pathlib import Path

from app.config import config_paths


def test_google_credentials_candidates_include_appdata_legacy_and_source(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    candidates = config_paths.google_credentials_candidates()

    assert candidates[0] == tmp_path / "Roaming" / "SansebasNexus" / "secrets" / "gmail_credentials.json"
    assert candidates[1] == tmp_path / "Roaming" / "NotionSecondBrain" / "secrets" / "gmail_credentials.json"
    assert candidates[2] == Path(config_paths.__file__).resolve().parents[2] / "secrets" / "gmail_credentials.json"


def test_find_google_credentials_prefers_sansebas_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    preferred = tmp_path / "Roaming" / "SansebasNexus" / "secrets" / "gmail_credentials.json"
    legacy = tmp_path / "Roaming" / "NotionSecondBrain" / "secrets" / "gmail_credentials.json"
    preferred.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    preferred.write_text('{"preferred": true}', encoding="utf-8")
    legacy.write_text('{"legacy": true}', encoding="utf-8")

    assert config_paths.find_google_credentials() == preferred


def test_copy_google_credentials_to_sansebas_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    source = tmp_path / "client_secret.json"
    source.write_text('{"installed": {}}', encoding="utf-8")

    destination = config_paths.copy_google_credentials(source)

    assert destination == tmp_path / "Roaming" / "SansebasNexus" / "secrets" / "gmail_credentials.json"
    assert destination.read_text(encoding="utf-8") == '{"installed": {}}'
