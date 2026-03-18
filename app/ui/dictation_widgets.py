"""Reusable UI helpers to attach toggle voice dictation to Tk widgets."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.services.voice_dictation import VoiceDictationError, VoiceDictationService

logger = logging.getLogger(__name__)


_last_focused_widget: tk.Widget | object | None = None


def _sanitize_tk_color(color: str | None, fallback: str = "#000000") -> str:
    """Return a Tkinter-safe color value for known invalid system color aliases."""
    value = str(color or "").strip()
    if not value:
        return fallback
    if value.lower() in {"windowtext"}:
        return fallback
    return value


def _es_widget_texto(widget: tk.Widget | object) -> bool:
    return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ScrolledText)) or all(
        hasattr(widget, attr) for attr in ("insert",)
    )


def _widget_exists(widget: tk.Widget | tk.Misc | object | None) -> bool:
    if widget is None or not hasattr(widget, "winfo_exists"):
        return False
    try:
        return bool(widget.winfo_exists())
    except Exception:  # noqa: BLE001
        return False


def safe_configure(widget: tk.Widget | object | None, **kwargs: object) -> None:
    if not _widget_exists(widget) or not hasattr(widget, "configure"):
        return
    try:
        widget.configure(**kwargs)
    except Exception:  # noqa: BLE001
        logger.debug("safe_configure ignoró error en widget destruido", exc_info=True)


def register_dictation_focus(widget: tk.Widget) -> None:
    """Registra un widget para que sea considerado destino de dictado cuando reciba foco."""

    def _remember_focus(event: tk.Event) -> None:
        global _last_focused_widget
        _last_focused_widget = event.widget

    widget.bind("<FocusIn>", _remember_focus, add="+")


def attach_dictation(widget: tk.Widget, parent_frame: tk.Misc) -> ttk.Frame:
    """Attach toggle dictation controls for Entry/Text/ScrolledText widgets."""
    if not _es_widget_texto(widget):
        raise ValueError("Dictado soporta solo Entry, Text y Textbox.")

    register_dictation_focus(widget)

    controls = ttk.Frame(parent_frame)
    style = ttk.Style(controls)
    style.configure(
        "DictationRecording.TButton",
        foreground=_sanitize_tk_color("#ffffff"),
        background=_sanitize_tk_color("#dc2626"),
    )

    mic_button = ttk.Button(controls, text="🎙", width=3)
    mic_button.pack(side="left")
    indicator = ttk.Label(controls, text="", foreground=_sanitize_tk_color("#B91C1C"))
    indicator.pack(side="left", padx=(6, 0))

    def _set_status(text: str) -> None:
        try:
            safe_configure(indicator, text="" if text == "Listo" else text)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo actualizar indicador de dictado", exc_info=True)

    def _set_button_state(recording: bool) -> None:
        try:
            safe_configure(mic_button, style="DictationRecording.TButton" if recording else "TButton")
            safe_configure(mic_button, text="⏹" if recording else "🎙")
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo actualizar botón de dictado", exc_info=True)

    def _show_error(msg: str) -> None:
        if not msg or not _widget_exists(controls):
            return
        try:
            messagebox.showwarning("Dictado", msg, parent=controls.winfo_toplevel())
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo mostrar error de dictado", exc_info=True)

    voice_service = VoiceDictationService(
        controls.winfo_toplevel(),
        status_callback=_set_status,
        button_state_callback=_set_button_state,
        error_callback=_show_error,
    )

    def _on_mic_click() -> None:
        try:
            voice_service.toggle_recording()
        except VoiceDictationError as exc:
            logger.exception("Error en dictado")
            _show_error(str(exc))

    def _on_controls_destroy(_event: tk.Event | None = None) -> None:
        try:
            voice_service.destroy()
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo destruir servicio de dictado", exc_info=True)

    controls.bind("<Destroy>", _on_controls_destroy, add="+")
    mic_button.configure(command=_on_mic_click)
    return controls
