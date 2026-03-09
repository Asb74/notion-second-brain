"""Reusable UI helpers to attach push-to-talk dictation to Tk widgets."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.services.voice_dictation import VoiceDictationError, dictar_texto

logger = logging.getLogger(__name__)

_ANIMATION_STEPS = ("🎤 Escuchando.", "🎤 Escuchando..", "🎤 Escuchando...")


def _es_widget_texto(widget: tk.Widget | object) -> bool:
    return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ScrolledText)) or all(
        hasattr(widget, attr) for attr in ("insert", "delete")
    )


def _insertar_transcripcion(widget: tk.Widget | object, text: str) -> None:
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
        return

    if hasattr(widget, "index"):
        try:
            insert_at = widget.index("insert")
            widget.insert(insert_at, text)
        except Exception:  # noqa: BLE001
            widget.insert("end", text)
    else:
        widget.insert("end", text)


def attach_dictation(widget: tk.Widget, parent_frame: tk.Misc) -> ttk.Frame:
    """Attach push-to-talk dictation controls for Entry/Text/ScrolledText widgets."""
    if not _es_widget_texto(widget):
        raise ValueError("Dictado soporta solo Entry, Text y Textbox.")

    controls = ttk.Frame(parent_frame)
    mic_button = ttk.Button(controls, text="🎙")
    mic_button.pack(side="left")
    indicator = ttk.Label(controls, text="", foreground="#B91C1C")
    indicator.pack(side="left", padx=(6, 0))

    animation_job: str | None = None
    animation_step = 0
    target_widget: tk.Widget | object = widget

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

    def _on_press(_event: tk.Event | None = None) -> None:
        nonlocal target_widget
        focused = controls.focus_get()
        if focused is not None and _es_widget_texto(focused):
            target_widget = focused
        else:
            target_widget = widget

        try:
            dictar_texto("iniciar")
            mic_button.configure(text="⏺")
            indicator.configure(text="🎤 Escuchando...")
            _animate()
        except VoiceDictationError as exc:
            logger.exception("Error en dictado")
            messagebox.showwarning("Dictado", str(exc), parent=controls.winfo_toplevel())

    def _on_release(_event: tk.Event | None = None) -> None:
        _stop_animation()
        indicator.configure(text="Transcribiendo...")

        def _worker() -> None:
            try:
                text = dictar_texto("detener")
                controls.after(0, lambda: _on_transcribed(text))
            except VoiceDictationError as exc:
                logger.exception("Error en dictado")
                controls.after(0, lambda: _on_error(str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_transcribed(text: str) -> None:
        mic_button.configure(text="🎙")
        indicator.configure(text="")
        if text:
            _insertar_transcripcion(target_widget, text)
            logger.info("Dictado insertado en widget")

    def _on_error(message: str) -> None:
        mic_button.configure(text="🎙")
        indicator.configure(text="")
        messagebox.showwarning("Dictado", message or "No se pudo transcribir el audio", parent=controls.winfo_toplevel())

    mic_button.bind("<ButtonPress-1>", _on_press)
    mic_button.bind("<ButtonRelease-1>", _on_release)
    mic_button.bind("<Leave>", _on_release)
    return controls
