"""Manual bulk OCR dialog for pending Knowledge attachments."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from collections.abc import Callable

from app.persistence.knowledge_repository import KnowledgeRepository
from app.services.knowledge_ocr_service import is_ocr_available

logger = logging.getLogger(__name__)


class KnowledgeBulkOcrDialog(tk.Toplevel):
    """Run controlled OCR for Knowledge attachments that are still pending."""

    def __init__(self, parent: tk.Misc, repo: KnowledgeRepository, on_finished: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.on_finished = on_finished
        self.cancel_event = threading.Event()
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = False

        self.title("OCR masivo pendientes")
        self.geometry("760x560")
        self.minsize(680, 500)
        self.transient(parent)

        self.images_var = tk.BooleanVar(value=True)
        self.pdfs_var = tk.BooleanVar(value=True)
        self.force_var = tk.BooleanVar(value=False)
        self.max_attachments_var = tk.IntVar(value=100)
        self.max_pdf_pages_var = tk.IntVar(value=5)
        self.notes_var = tk.StringVar(value="—")
        self.attachments_var = tk.StringVar(value="—")
        self.current_note_var = tk.StringVar(value="—")
        self.current_attachment_var = tk.StringVar(value="—")
        self.progress_var = tk.StringVar(value="0 / 0")
        self.errors_var = tk.StringVar(value="0")
        self.summary_var = tk.StringVar(value="Configura filtros y pulsa Iniciar.")

        self._build_ui()
        self._refresh_counts()
        self.protocol("WM_DELETE_WINDOW", self._close_or_cancel)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        stats = ttk.LabelFrame(root, text="Candidatos", padding=8)
        stats.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        stats.columnconfigure(1, weight=1)
        ttk.Label(stats, text="Notas con adjuntos candidatos:").grid(row=0, column=0, sticky="w")
        ttk.Label(stats, textvariable=self.notes_var).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(stats, text="Adjuntos pendientes:").grid(row=1, column=0, sticky="w")
        ttk.Label(stats, textvariable=self.attachments_var).grid(row=1, column=1, sticky="w", padx=(8, 0))

        filters = ttk.LabelFrame(root, text="Filtros", padding=8)
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(filters, text="Imágenes", variable=self.images_var, command=self._refresh_counts).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(filters, text="PDFs escaneados", variable=self.pdfs_var, command=self._refresh_counts).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(filters, text="Reprocesar aunque ya tenga OCR", variable=self.force_var, command=self._refresh_counts).pack(side="left")

        limits = ttk.LabelFrame(root, text="Límites", padding=8)
        limits.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(limits, text="Máximo adjuntos a procesar").pack(side="left")
        ttk.Spinbox(limits, from_=1, to=100000, textvariable=self.max_attachments_var, width=8, command=self._refresh_counts).pack(side="left", padx=(6, 18))
        ttk.Label(limits, text="Máximo páginas PDF por archivo").pack(side="left")
        ttk.Spinbox(limits, from_=1, to=200, textvariable=self.max_pdf_pages_var, width=8).pack(side="left", padx=(6, 0))

        progress = ttk.LabelFrame(root, text="Progreso", padding=8)
        progress.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        progress.columnconfigure(1, weight=1)
        ttk.Label(progress, text="Nota actual:").grid(row=0, column=0, sticky="w")
        ttk.Label(progress, textvariable=self.current_note_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(progress, text="Adjunto actual:").grid(row=1, column=0, sticky="w")
        ttk.Label(progress, textvariable=self.current_attachment_var).grid(row=1, column=1, sticky="ew")
        ttk.Label(progress, text="Procesados / total:").grid(row=2, column=0, sticky="w")
        ttk.Label(progress, textvariable=self.progress_var).grid(row=2, column=1, sticky="w")
        ttk.Label(progress, text="Errores:").grid(row=3, column=0, sticky="w")
        ttk.Label(progress, textvariable=self.errors_var).grid(row=3, column=1, sticky="w")

        self.log_text = ScrolledText(root, wrap="word", height=12)
        self.log_text.grid(row=4, column=0, sticky="nsew", pady=(0, 8))
        self.log_text.configure(state="disabled")
        ttk.Label(root, textvariable=self.summary_var).grid(row=5, column=0, sticky="ew", pady=(0, 8))

        buttons = ttk.Frame(root)
        buttons.grid(row=6, column=0, sticky="e")
        self.start_button = ttk.Button(buttons, text="Iniciar", command=self._start)
        self.start_button.pack(side="left", padx=(0, 6))
        self.cancel_button = ttk.Button(buttons, text="Cancelar", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 6))
        self.close_button = ttk.Button(buttons, text="Cerrar", command=self._close_or_cancel)
        self.close_button.pack(side="left")

    def _options(self) -> dict[str, object]:
        return {
            "include_images": bool(self.images_var.get()),
            "include_pdfs": bool(self.pdfs_var.get()),
            "force": bool(self.force_var.get()),
            "limit": max(1, int(self.max_attachments_var.get() or 1)),
            "max_pdf_pages": max(1, int(self.max_pdf_pages_var.get() or 5)),
        }

    def _refresh_counts(self) -> None:
        try:
            options = self._options()
            options.pop("limit", None)
            summary = self.repo.count_bulk_ocr_candidates(**options)
            self.notes_var.set(str(summary["notes"]))
            self.attachments_var.set(str(summary["attachments"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_BULK_OCR: count error=%s", exc)
            self.summary_var.set(f"No se pudieron calcular candidatos: {exc}")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start(self) -> None:
        if self.running:
            return
        available, reason = is_ocr_available()
        if not available:
            messagebox.showwarning("OCR masivo", reason, parent=self)
            self.summary_var.set(reason)
            return
        self.running = True
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.summary_var.set("OCR masivo en curso...")
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self.after(150, self._drain_events)

    def _worker(self) -> None:
        def progress(event: dict[str, object]) -> None:
            self.events.put(event)

        try:
            result = self.repo.bulk_ocr_pending_attachments(cancel_event=self.cancel_event, progress_callback=progress, **self._options())
            self.events.put({"type": "finished", "result": result})
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_BULK_OCR: worker failed")
            self.events.put({"type": "failed", "error": exc})

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = str(event.get("type") or "")
            if kind == "progress":
                self.current_note_var.set(str(event.get("note") or "—"))
                self.current_attachment_var.set(str(event.get("attachment") or "—"))
                self.progress_var.set(f"{event.get('processed', 0)} / {event.get('total', 0)}")
                self.errors_var.set(str(event.get("errors") or 0))
            elif kind == "log":
                self._append_log(str(event.get("message") or ""))
            elif kind == "finished":
                self._finish(event.get("result"))
            elif kind == "failed":
                self._fail(event.get("error"))
        if self.running:
            self.after(150, self._drain_events)

    def _finish(self, result: object) -> None:
        self.running = False
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        data = result if isinstance(result, dict) else {}
        summary = (
            f"Finalizado: candidatos={data.get('candidates', 0)}, procesados={data.get('processed', 0)}, "
            f"ok={data.get('ok', 0)}, sin texto={data.get('empty', 0)}, errores={data.get('errors', 0)}, "
            f"omitidos={data.get('skipped', 0)}, tiempo={float(data.get('seconds', 0.0)):.1f}s"
        )
        if data.get("cancelled"):
            summary = "Cancelado. " + summary
        self.summary_var.set(summary)
        self._append_log(summary)
        self._refresh_counts()
        if self.on_finished is not None:
            self.on_finished()

    def _fail(self, error: object) -> None:
        self.running = False
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.summary_var.set(f"Error: {error}")
        messagebox.showerror("OCR masivo", f"No se pudo ejecutar OCR masivo.\n\n{error}", parent=self)

    def _cancel(self) -> None:
        if self.running:
            self.cancel_event.set()
            self.summary_var.set("Cancelando al terminar el adjunto actual...")
            self._append_log("Cancelación solicitada; se detendrá antes del siguiente adjunto.")

    def _close_or_cancel(self) -> None:
        if self.running:
            self._cancel()
            return
        self.destroy()
