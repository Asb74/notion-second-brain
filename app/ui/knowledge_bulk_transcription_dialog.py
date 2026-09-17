"""Cancelable background transcription of historical Knowledge audio."""

from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from app.persistence.knowledge_repository import KnowledgeRepository


class KnowledgeBulkTranscriptionDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        repo: KnowledgeRepository,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo, self.on_finished = repo, on_finished
        self.cancel_event = threading.Event()
        self.rows = repo.list_audio_attachments(pending_only=True)
        self.selected: dict[int, tk.BooleanVar] = {}
        self.title("Transcripción masiva")
        self.geometry("720x520")
        self.transient(parent)
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        ttk.Label(
            root,
            text=f"Audios encontrados: {len(repo.list_audio_attachments())}   Pendientes: {len(self.rows)}",
        ).pack(anchor="w", pady=(0, 8))
        canvas = tk.Canvas(root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=frame, anchor="nw")
        for row in self.rows:
            attachment_id = int(row["id"])
            var = tk.BooleanVar(value=True)
            self.selected[attachment_id] = var
            ttk.Checkbutton(
                frame,
                variable=var,
                text=f"{row['original_filename']} — {row['transcript_status'] or 'pendiente'}",
            ).pack(anchor="w")
        frame.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        self.status_var = tk.StringVar(
            value="Selecciona los audios que deseas transcribir."
        )
        ttk.Label(root, textvariable=self.status_var).pack(fill="x", pady=8)
        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 8))
        buttons = ttk.Frame(root)
        buttons.pack(anchor="e")
        self.start_button = ttk.Button(
            buttons, text="Transcribir seleccionados", command=self._start
        )
        self.start_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancelar", command=self._cancel).pack(
            side="left", padx=4
        )

    def _start(self) -> None:
        ids = [
            attachment_id for attachment_id, var in self.selected.items() if var.get()
        ]
        if not ids:
            return
        self.start_button.configure(state="disabled")
        self.progress.configure(maximum=len(ids), value=0)
        threading.Thread(target=self._worker, args=(ids,), daemon=True).start()

    def _worker(self, ids: list[int]) -> None:
        result = self.repo.bulk_transcribe(
            ids, cancel_event=self.cancel_event, progress=self._progress
        )
        self.after(0, self._finished, result)

    def _progress(self, current: int, total: int, _attachment_id: int) -> None:
        self.after(
            0,
            lambda: (
                self.progress.configure(value=current - 1),
                self.status_var.set(f"Transcribiendo audio {current} de {total}..."),
            ),
        )

    def _finished(self, result: dict[str, object]) -> None:
        self.progress.configure(
            value=self.progress["maximum"] - int(result.get("pending") or 0)
        )
        self.status_var.set(
            f"OK: {result['ok']}  Vacíos: {result['empty']}  Errores: {result['errors']}  Pendientes: {result['pending']}"
        )
        self.start_button.configure(state="normal")
        if self.on_finished:
            self.on_finished()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Cancelación solicitada; se detendrá entre archivos.")
