"""Main Tkinter window."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

from app.core.models import AppSettings, NoteCreateRequest
from app.core.service import NoteService
from app.ui.settings_dialog import SettingsDialog


class MainWindow(ttk.Frame):
    """Primary app UI with note form and sync status list."""

    def __init__(self, master: tk.Tk, service: NoteService):
        super().__init__(master, padding=10)
        self.master = master
        self.service = service
        self.msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.pack(fill="both", expand=True)

        self._build_form()
        self._build_list()
        self.refresh_notes()
        self.after(150, self._poll_queue)

    def _build_form(self) -> None:
        form = ttk.LabelFrame(self, text="Nueva nota")
        form.pack(fill="x", pady=5)

        self.source_var = tk.StringVar(value="manual")
        self.area_var = tk.StringVar()
        self.tipo_var = tk.StringVar()
        self.estado_var = tk.StringVar(value="Pendiente")
        self.prioridad_var = tk.StringVar(value="Media")
        self.fecha_var = tk.StringVar(value=datetime.now().date().isoformat())
        self.title_var = tk.StringVar()

        ttk.Label(form, text="Título").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(form, textvariable=self.title_var, width=40).grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(form, text="Fuente").grid(row=0, column=2, padx=4, pady=4)
        ttk.Combobox(form, textvariable=self.source_var, values=["manual", "email_pasted"], state="readonly", width=15).grid(row=0, column=3, padx=4, pady=4)

        self.text_widget = tk.Text(form, height=10, width=100)
        self.text_widget.grid(row=1, column=0, columnspan=4, sticky="ew", padx=4, pady=4)

        for i, (label, var) in enumerate(
            [
                ("Área", self.area_var),
                ("Tipo", self.tipo_var),
                ("Estado", self.estado_var),
                ("Prioridad", self.prioridad_var),
                ("Fecha", self.fecha_var),
            ]
        ):
            ttk.Label(form, text=label).grid(row=2, column=i * 2, padx=4, pady=4, sticky="e")
            ttk.Entry(form, textvariable=var, width=15).grid(row=2, column=i * 2 + 1, padx=4, pady=4)

        actions = ttk.Frame(form)
        actions.grid(row=3, column=0, columnspan=6, sticky="e", pady=6)
        ttk.Button(actions, text="Configuración", command=self._open_settings).pack(side="left", padx=3)
        ttk.Button(actions, text="Guardar", command=self._save_note).pack(side="left", padx=3)
        ttk.Button(actions, text="Enviar", command=self._sync).pack(side="left", padx=3)
        ttk.Button(actions, text="Reintentar", command=self._sync).pack(side="left", padx=3)
        ttk.Button(actions, text="Abrir en Notion", command=self._open_notion).pack(side="left", padx=3)

    def _build_list(self) -> None:
        frame = ttk.LabelFrame(self, text="Notas")
        frame.pack(fill="both", expand=True, pady=8)
        columns = ("id", "title", "status", "error", "notion_page_id")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        for c in columns:
            self.tree.heading(c, text=c)
        self.tree.column("id", width=40)
        self.tree.column("title", width=260)
        self.tree.column("status", width=90)
        self.tree.column("error", width=260)
        self.tree.column("notion_page_id", width=220)
        self.tree.pack(fill="both", expand=True)

    def _open_settings(self) -> None:
        current = self.service.get_settings()

        def on_save(new_settings: AppSettings) -> None:
            self.service.save_settings(new_settings)
            self._apply_defaults(new_settings)

        SettingsDialog(self.master, current, on_save)

    def _apply_defaults(self, settings: AppSettings) -> None:
        if settings.default_area:
            self.area_var.set(settings.default_area)
        if settings.default_tipo:
            self.tipo_var.set(settings.default_tipo)
        if settings.default_estado:
            self.estado_var.set(settings.default_estado)
        if settings.default_prioridad:
            self.prioridad_var.set(settings.default_prioridad)

    def _save_note(self) -> None:
        raw_text = self.text_widget.get("1.0", "end").strip()
        if not raw_text:
            messagebox.showwarning("Validación", "El texto de la nota es obligatorio.")
            return

        req = NoteCreateRequest(
            title=self.title_var.get().strip(),
            raw_text=raw_text,
            source=self.source_var.get(),
            area=self.area_var.get().strip() or "General",
            tipo=self.tipo_var.get().strip() or "Nota",
            estado=self.estado_var.get().strip() or "Pendiente",
            prioridad=self.prioridad_var.get().strip() or "Media",
            fecha=self.fecha_var.get().strip() or datetime.now().date().isoformat(),
        )
        note_id, msg = self.service.create_note(req)
        if note_id is None:
            messagebox.showinfo("Duplicado", msg)
        else:
            messagebox.showinfo("OK", msg)
            self.text_widget.delete("1.0", "end")
            self.title_var.set("")
        self.refresh_notes()

    def _sync(self) -> None:
        threading.Thread(target=self._sync_worker, daemon=True).start()

    def _sync_worker(self) -> None:
        try:
            sent, failed = self.service.sync_pending()
            self.msg_queue.put(("info", f"Sincronización completada. Enviadas: {sent}, Errores: {failed}"))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, msg = self.msg_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                messagebox.showerror("Error", msg)
            else:
                messagebox.showinfo("Resultado", msg)
            self.refresh_notes()
        self.after(150, self._poll_queue)

    def refresh_notes(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        for note in self.service.list_notes():
            self.tree.insert("", "end", iid=str(note.id), values=(note.id, note.title, note.status, note.last_error or "", note.notion_page_id or ""))

    def _open_notion(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona una nota.")
            return
        note_id = int(sel[0])
        note = next((n for n in self.service.list_notes() if n.id == note_id), None)
        if not note or not note.notion_page_id:
            messagebox.showwarning("Atención", "La nota no tiene página de Notion vinculada.")
            return
        webbrowser.open(f"https://www.notion.so/{note.notion_page_id.replace('-', '')}")
