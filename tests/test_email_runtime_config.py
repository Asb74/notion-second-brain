import json
from pathlib import Path

from app.config import email_runtime_config


def test_load_config_creates_default_when_missing(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config_email.json"
    monkeypatch.setattr(email_runtime_config, "_config_path", lambda: config_path)

    config = email_runtime_config.load_config()

    assert config["enabled"] is True
    assert config["check_interval"] == 60
    assert config["notifications"] is True
    assert config_path.exists()


def test_save_and_load_config_normalizes_values(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config_email.json"
    monkeypatch.setattr(email_runtime_config, "_config_path", lambda: config_path)

    email_runtime_config.save_config({"enabled": 1, "check_interval": 1, "notifications": 0})
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["check_interval"] == 10

    loaded = email_runtime_config.load_config()
    assert loaded == {"enabled": True, "check_interval": 10, "notifications": False}
