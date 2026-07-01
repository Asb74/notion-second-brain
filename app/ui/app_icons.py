"""Reusable icon helpers for Tkinter windows."""

from __future__ import annotations

import logging
from pathlib import Path
import tkinter as tk

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
ICON_PATH = ASSETS_DIR / "icono_app.ico"
PNG_ICON_PATH = ASSETS_DIR / "icono_app.png"

logger = logging.getLogger(__name__)

_icon_cache: tk.PhotoImage | None = None
_named_icon_cache: dict[str, tk.PhotoImage | None] = {}


def load_icon(name: str) -> tk.PhotoImage | None:
    """Load an icon from assets by filename and cache it."""
    if name not in _named_icon_cache:
        path = ASSETS_DIR / name
        logger.info("ICON_DEBUG: intentando cargar icono %s", path)
        try:
            _named_icon_cache[name] = tk.PhotoImage(file=str(path))
            logger.info("ICON_DEBUG: icono cargado correctamente")
        except Exception as exc:  # noqa: BLE001
            logger.info("ICON_DEBUG: error cargando icono %s: %s", path, exc)
            _named_icon_cache[name] = None
    return _named_icon_cache[name]


def get_app_icon() -> tk.PhotoImage | None:
    """Return fallback PNG app icon, loaded once and cached."""
    global _icon_cache

    if _icon_cache is None:
        _icon_cache = load_icon(PNG_ICON_PATH.name)

    return _icon_cache


def apply_app_icon(window: tk.Misc) -> None:
    """Apply global app icon to a Tk/Toplevel window when available."""
    try:
        title = window.title() if hasattr(window, "title") else ""
    except Exception:  # noqa: BLE001
        title = ""
    logger.info("ICON_DEBUG aplicado a ventana secundaria title=%s", title)
    logger.info("ICON_DEBUG: intentando cargar icono %s", ICON_PATH)
    try:
        window.iconbitmap(str(ICON_PATH))
        logger.info("ICON_DEBUG: icono cargado correctamente")
        return
    except Exception as exc:  # noqa: BLE001
        logger.info("ICON_DEBUG: error cargando icono %s: %s", ICON_PATH, exc)

    icon = get_app_icon()

    if icon:
        try:
            window.iconphoto(True, icon)
        except Exception as exc:  # noqa: BLE001
            logger.info("ICON_DEBUG: error cargando icono %s: %s", PNG_ICON_PATH, exc)
