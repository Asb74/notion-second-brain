"""Reusable icon helpers for Tkinter windows."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
ICON_PATH = ASSETS_DIR / "icono_app.png"

_icon_cache: tk.PhotoImage | None = None
_named_icon_cache: dict[str, tk.PhotoImage | None] = {}


def load_icon(name: str) -> tk.PhotoImage | None:
    """Load an icon from assets by filename and cache it."""
    if name not in _named_icon_cache:
        path = ASSETS_DIR / name
        try:
            _named_icon_cache[name] = tk.PhotoImage(file=str(path))
        except Exception:  # noqa: BLE001
            _named_icon_cache[name] = None
    return _named_icon_cache[name]


def get_app_icon() -> tk.PhotoImage | None:
    """Return app icon, loaded once and cached."""
    global _icon_cache

    if _icon_cache is None:
        _icon_cache = load_icon(ICON_PATH.name)

    return _icon_cache


def apply_app_icon(window: tk.Misc) -> None:
    """Apply global app icon to a Tk/Toplevel window when available."""
    icon = get_app_icon()

    if icon:
        try:
            window.iconphoto(True, icon)
        except Exception:  # noqa: BLE001
            pass
