"""Runtime configuration for background email polling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_EMAIL_CONFIG: dict[str, Any] = {
    "enabled": True,
    "check_interval": 60,
    "notifications": True,
}


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config_email.json"


def load_config() -> dict[str, Any]:
    """Load email runtime configuration from JSON, creating defaults when missing."""
    config_path = _config_path()
    if not config_path.exists():
        save_config(DEFAULT_EMAIL_CONFIG)
        return dict(DEFAULT_EMAIL_CONFIG)

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        save_config(DEFAULT_EMAIL_CONFIG)
        return dict(DEFAULT_EMAIL_CONFIG)

    config = dict(DEFAULT_EMAIL_CONFIG)
    if isinstance(raw, dict):
        config["enabled"] = bool(raw.get("enabled", DEFAULT_EMAIL_CONFIG["enabled"]))
        try:
            config["check_interval"] = max(10, int(raw.get("check_interval", DEFAULT_EMAIL_CONFIG["check_interval"])))
        except (TypeError, ValueError):
            config["check_interval"] = DEFAULT_EMAIL_CONFIG["check_interval"]
        config["notifications"] = bool(raw.get("notifications", DEFAULT_EMAIL_CONFIG["notifications"]))

    if config != raw:
        save_config(config)
    return config


def save_config(config: dict[str, Any]) -> None:
    """Persist email runtime configuration to JSON."""
    normalized = {
        "enabled": bool(config.get("enabled", DEFAULT_EMAIL_CONFIG["enabled"])),
        "check_interval": max(10, int(config.get("check_interval", DEFAULT_EMAIL_CONFIG["check_interval"]))),
        "notifications": bool(config.get("notifications", DEFAULT_EMAIL_CONFIG["notifications"])),
    }
    _config_path().write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
