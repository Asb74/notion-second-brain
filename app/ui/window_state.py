"""Persistent Tkinter window state helpers."""

from __future__ import annotations

import json
import logging
import tkinter as tk

from app.config.config_paths import app_data_dir

logger = logging.getLogger(__name__)

_STATE_FILE = app_data_dir() / "config" / "window_state.json"
_MIN_VALID_WIDTH = 300
_MIN_VALID_HEIGHT = 200


def _load_all() -> dict[str, object]:
    try:
        if not _STATE_FILE.exists():
            return {}
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.info("WINDOW_STATE: ignoring unreadable state file path=%s", _STATE_FILE)
        return {}
    return data if isinstance(data, dict) else {}


def load_window_state(window_key: str) -> dict[str, object]:
    """Load saved state for a named window, ignoring corrupt data."""
    state = _load_all().get(window_key)
    return dict(state) if isinstance(state, dict) else {}


def save_window_state(window_key: str, state: dict[str, object]) -> None:
    """Persist state for a named window without raising UI errors."""
    try:
        data = _load_all()
        data[window_key] = state
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        logger.info(
            "WINDOW_STATE: could not save state path=%s", _STATE_FILE, exc_info=True
        )


def is_valid_window_geometry(window: tk.Misc) -> bool:
    """Return whether the current Tk geometry is useful to persist."""
    try:
        width = int(window.winfo_width())
        height = int(window.winfo_height())
        x = int(window.winfo_x())
        y = int(window.winfo_y())
        screen_width = int(window.winfo_screenwidth())
        screen_height = int(window.winfo_screenheight())
    except Exception:  # noqa: BLE001
        return False

    if width < _MIN_VALID_WIDTH or height < _MIN_VALID_HEIGHT:
        return False
    if x < -width or y < -height:
        return False
    if x > screen_width or y > screen_height:
        return False
    return True
