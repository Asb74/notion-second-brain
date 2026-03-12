"""Reusable UI helpers to attach toggle voice dictation to Tk widgets."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.services.voice_dictation import VoiceDictationError, VoiceDictationService

logger = logging.getLogger(__name__)


def _sanitize_tk_color(color: str | None, fallback: str = "#000000") -> str:
    """Return a Tkinter-safe color value for known invalid system color aliases."""
    value = str(color or "").strip()
    if not value:
        return fallback
    if value.lower() == "windowtext":
        return fallback
    return value


def _es_widget_texto(widget: tk.Widget | object) -> bool:
    return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ScrolledText)) or all(
        hasattr(widget, attr) for attr in ("insert",)
    )


def attach_dictation(widget: tk.Widget, parent_frame: tk.Misc) -> ttk.Frame:
    """Attach toggle dictation controls for Entry/Text/ScrolledText widgets."""
    if not _es_widget_texto(widget):
        raise ValueError("Dictado soporta solo Entry, Text y Textbox.")

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
        indicator.configure(text="" if text == "Listo" else text)

    def _set_button_state(recording: bool) -> None:
        mic_button.configure(style="DictationRecording.TButton" if recording else "TButton")

    def _show_error(msg: str) -> None:
        messagebox.showwarning("Dictado", msg, parent=controls.winfo_toplevel())

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

    mic_button.configure(command=_on_mic_click)
    return controls
