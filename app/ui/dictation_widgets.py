"""Reusable UI helpers to attach push-to-talk dictation to Tk widgets."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.services.dictation_service import DictationError, DictationService

logger = logging.getLogger(__name__)

_ANIMATION_STEPS = ("● Escuchando.", "● Escuchando..", "● Escuchando...")


def attach_dictation(widget: tk.Widget, parent_frame: tk.Misc, dictation_service: DictationService) -> ttk.Frame:
    """Attach push-to-talk dictation controls for Entry/Text/ScrolledText widgets."""
    if not isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ScrolledText)):
        raise ValueError("Dictado soporta solo Entry, Text y ScrolledText.")

    controls = ttk.Frame(parent_frame)
    mic_button = ttk.Button(controls, text="🎙")
    mic_button.pack(side="left")
    indicator = ttk.Label(controls, text="", foreground="#B91C1C")
    indicator.pack(side="left", padx=(6, 0))

    animation_job: str | None = None
    animation_step = 0

    def _animate() -> None:
        nonlocal animation_job, animation_step
        indicator.configure(text=_ANIMATION_STEPS[animation_step % len(_ANIMATION_STEPS)])
        animation_step += 1
        animation_job = controls.after(350, _animate)

    def _stop_animation() -> None:
        nonlocal animation_job
        if animation_job is not None:
            controls.after_cancel(animation_job)
            animation_job = None

    def _insert_transcription(text: str) -> None:
        if not text:
            return

        if isinstance(widget, (tk.Entry, ttk.Entry)):
            try:
                start = widget.index("sel.first")
                end = widget.index("sel.last")
                widget.delete(start, end)
                widget.insert(start, text)
            except tk.TclError:
                widget.insert(widget.index("insert"), text)
            return

        if isinstance(widget, (tk.Text, ScrolledText)):
            try:
                widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            widget.insert("insert", text)

    def _on_press(_event: tk.Event | None = None) -> None:
        if dictation_service.is_recording():
            return
        try:
            dictation_service.start_recording()
            mic_button.configure(text="⏺")
            indicator.configure(text="● Escuchando...")
            _animate()
        except DictationError as exc:
            logger.exception("Error en dictado")
            messagebox.showwarning("Dictado", str(exc), parent=controls.winfo_toplevel())

    def _on_release(_event: tk.Event | None = None) -> None:
        if not dictation_service.is_recording():
            return
        _stop_animation()
        indicator.configure(text="Procesando audio...")

        def _worker() -> None:
            try:
                text = dictation_service.stop_recording_and_transcribe()
                controls.after(0, lambda: _on_transcribed(text))
            except DictationError:
                logger.exception("Error en dictado")
                controls.after(0, _on_error)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_transcribed(text: str) -> None:
        mic_button.configure(text="🎙")
        indicator.configure(text="")
        if text:
            _insert_transcription(text)
            logger.info("Dictado insertado en widget")

    def _on_error() -> None:
        mic_button.configure(text="🎙")
        indicator.configure(text="")
        messagebox.showwarning("Dictado", "No se pudo transcribir el audio", parent=controls.winfo_toplevel())

    mic_button.bind("<ButtonPress-1>", _on_press)
    mic_button.bind("<ButtonRelease-1>", _on_release)
    mic_button.bind("<Leave>", _on_release)
    return controls
