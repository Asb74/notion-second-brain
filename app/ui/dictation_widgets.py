"""Reusable UI helpers to attach toggle voice dictation to Tk widgets."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.services.voice_dictation import VoiceDictationError, voice_dictation_service

logger = logging.getLogger(__name__)

_ANIMATION_STEPS = ("🎤 Escuchando.", "🎤 Escuchando..", "🎤 Escuchando...")


def _es_widget_texto(widget: tk.Widget | object) -> bool:
    return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ScrolledText)) or all(
        hasattr(widget, attr) for attr in ("insert",)
    )


def _insertar_transcripcion(widget: tk.Widget | object, text: str) -> None:
    if not text:
        return

    widget.insert("insert", text)


def attach_dictation(widget: tk.Widget, parent_frame: tk.Misc) -> ttk.Frame:
    """Attach toggle dictation controls for Entry/Text/ScrolledText widgets."""
    if not _es_widget_texto(widget):
        raise ValueError("Dictado soporta solo Entry, Text y Textbox.")

    controls = ttk.Frame(parent_frame)
    style = ttk.Style(controls)
    style.configure("DictationRecording.TButton", foreground="#ffffff", background="#dc2626")

    mic_button = ttk.Button(controls, text="🎙", width=3)
    mic_button.pack(side="left")
    indicator = ttk.Label(controls, text="", foreground="#B91C1C")
    indicator.pack(side="left", padx=(6, 0))

    animation_job: str | None = None
    animation_step = 0
    widget_destino: tk.Widget | object = widget

    def _animate() -> None:
        nonlocal animation_job, animation_step
        indicator.configure(text=_ANIMATION_STEPS[animation_step % len(_ANIMATION_STEPS)])
        animation_step += 1
        animation_job = controls.after(500, _animate)

    def _stop_animation() -> None:
        nonlocal animation_job
        if animation_job is not None:
            controls.after_cancel(animation_job)
            animation_job = None

    def _on_mic_click() -> None:
        nonlocal widget_destino
        if not voice_dictation_service.recording:
            root = controls.winfo_toplevel()
            focused = root.focus_get()
            if focused is not None and _es_widget_texto(focused):
                widget_destino = focused
            else:
                widget_destino = widget

            try:
                voice_dictation_service.start_recording()
                mic_button.configure(style="DictationRecording.TButton", text="⏹")
                indicator.configure(text="🎤 Escuchando...")
                _animate()
            except VoiceDictationError as exc:
                logger.exception("Error en dictado")
                messagebox.showwarning("Dictado", str(exc), parent=controls.winfo_toplevel())
            return

        _stop_animation()
        indicator.configure(text="⏳ Transcribiendo...")
        mic_button.state(["disabled"])

        def _worker() -> None:
            try:
                text = voice_dictation_service.stop_recording()
                controls.after(0, lambda: _on_transcribed(text))
            except VoiceDictationError:
                logger.exception("Error en dictado")
                controls.after(0, _on_error)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_transcribed(text: str) -> None:
        mic_button.state(["!disabled"])
        mic_button.configure(style="TButton", text="🎙")
        indicator.configure(text="")
        if text:
            _insertar_transcripcion(widget_destino, text)

    def _on_error() -> None:
        mic_button.state(["!disabled"])
        mic_button.configure(style="TButton", text="🎙")
        indicator.configure(text="")
        messagebox.showwarning("Dictado", "No se pudo transcribir el audio", parent=controls.winfo_toplevel())

    mic_button.configure(command=_on_mic_click)
    return controls
