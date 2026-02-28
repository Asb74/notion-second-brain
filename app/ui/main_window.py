"""Main Tkinter window."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from tkcalendar import DateEntry

from app.core.models import AppSettings, NoteCreateRequest
from app.core.service import NoteService
from app.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


class MainWindow(ttk.Frame):
    """Primary app UI with note form and sync status list."""

    def __init__(self, master: tk.Tk, service: NoteService):
        super().__init__(master, padding=10)
        self.master = master
        self.service = service
        self.msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.status_var = tk.StringVar(value="Listo")
        self.action_area_var = tk.StringVar(value="Todas")
        self.pack(fill="both", expand=True)

        self._build_form()
        self._build_sections()
        self._load_master_values()
        self._refresh_database_button_state()
        self.refresh_notes()
        self.refresh_actions()
        self.after(150, self._poll_queue)

    def _build_form(self) -> None:
        form = ttk.LabelFrame(self, text="Nueva nota")
        form.pack(fill="x", pady=5)

        self.source_var = tk.StringVar(value="manual")
        self.area_var = tk.StringVar()
        self.tipo_var = tk.StringVar()
        self.estado_var = tk.StringVar(value="Pendiente")
        self.prioridad_var = tk.StringVar(value="Media")
        self.title_var = tk.StringVar()

        ttk.Label(form, text="Título").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(form, textvariable=self.title_var, width=40).grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(form, text="Fuente").grid(row=0, column=2, padx=4, pady=4)
        ttk.Combobox(form, textvariable=self.source_var, values=["manual", "email_pasted"], state="readonly", width=15).grid(row=0, column=3, padx=4, pady=4)

        self.text_widget = tk.Text(form, height=10, width=100)
        self.text_widget.grid(row=1, column=0, columnspan=4, sticky="ew", padx=4, pady=4)

        ttk.Label(form, text="Área").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        self.area_combo = ttk.Combobox(form, textvariable=self.area_var, state="readonly", width=15)
        self.area_combo.grid(row=2, column=1, padx=4, pady=4)

        ttk.Label(form, text="Tipo").grid(row=2, column=2, padx=4, pady=4, sticky="e")
        self.tipo_combo = ttk.Combobox(form, textvariable=self.tipo_var, state="readonly", width=15)
        self.tipo_combo.grid(row=2, column=3, padx=4, pady=4)

        ttk.Label(form, text="Estado").grid(row=2, column=4, padx=4, pady=4, sticky="e")
        self.estado_combo = ttk.Combobox(form, textvariable=self.estado_var, state="readonly", width=15)
        self.estado_combo.grid(row=2, column=5, padx=4, pady=4)

        ttk.Label(form, text="Prioridad").grid(row=2, column=6, padx=4, pady=4, sticky="e")
        self.prioridad_combo = ttk.Combobox(form, textvariable=self.prioridad_var, state="readonly", width=15)
        self.prioridad_combo.grid(row=2, column=7, padx=4, pady=4)

        ttk.Label(form, text="Fecha").grid(row=2, column=8, padx=4, pady=4, sticky="e")
        self.date_entry = DateEntry(form, width=15, date_pattern="yyyy-mm-dd")
        self.date_entry.grid(row=2, column=9, padx=4, pady=4)

        actions = ttk.Frame(form)
        actions.grid(row=3, column=0, columnspan=10, sticky="e", pady=6)
        ttk.Button(actions, text="Configuración", command=self._open_settings).pack(side="left", padx=3)
        ttk.Button(actions, text="Guardar", command=self._save_note).pack(side="left", padx=3)
        ttk.Button(actions, text="Enviar", command=self._sync).pack(side="left", padx=3)
        ttk.Button(actions, text="Reintentar", command=self._sync).pack(side="left", padx=3)
        ttk.Button(actions, text="Abrir en Notion", command=self._open_notion).pack(side="left", padx=3)
        self.create_db_button = ttk.Button(actions, text="Crear Base Notion", command=self._create_notion_database)
        self.create_db_button.pack(side="left", padx=3)

    def _build_sections(self) -> None:
        sections = ttk.Notebook(self)
        sections.pack(fill="both", expand=True, pady=8)

        notes_frame = ttk.Frame(sections)
        actions_frame = ttk.Frame(sections)
        sections.add(notes_frame, text="Notas")
        sections.add(actions_frame, text="Acciones")

        columns = ("id", "title", "status", "error", "notion_page_id")
        self.tree = ttk.Treeview(notes_frame, columns=columns, show="headings", height=12)
        for c in columns:
            self.tree.heading(c, text=c)
        self.tree.column("id", width=40)
        self.tree.column("title", width=260)
        self.tree.column("status", width=90)
        self.tree.column("error", width=260)
        self.tree.column("notion_page_id", width=220)
        self.tree.pack(fill="both", expand=True)

        toolbar = ttk.Frame(actions_frame)
        toolbar.pack(fill="x", padx=4, pady=4)
        ttk.Label(toolbar, text="Filtrar Área:").pack(side="left", padx=(0, 6))
        self.action_area_combo = ttk.Combobox(toolbar, textvariable=self.action_area_var, state="readonly", width=22)
        self.action_area_combo.pack(side="left")
        self.action_area_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_actions())
        ttk.Button(toolbar, text="Marcar como hecha", command=self._mark_selected_action_done).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Refrescar", command=self.refresh_actions).pack(side="left", padx=6)

        action_columns = ("id", "area", "description", "status", "note_id")
        self.actions_tree = ttk.Treeview(actions_frame, columns=action_columns, show="headings", height=12)
        self.actions_tree.heading("id", text="ID")
        self.actions_tree.heading("area", text="Área")
        self.actions_tree.heading("description", text="Descripción")
        self.actions_tree.heading("status", text="Estado")
        self.actions_tree.heading("note_id", text="Nota asociada")
        self.actions_tree.column("id", width=50)
        self.actions_tree.column("area", width=140)
        self.actions_tree.column("description", width=420)
        self.actions_tree.column("status", width=100)
        self.actions_tree.column("note_id", width=130)
        self.actions_tree.pack(fill="both", expand=True, padx=4, pady=4)

        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(2, 0))

    def _load_master_values(self) -> None:
        area_values = self.service.get_master_values("Area")
        tipo_values = self.service.get_master_values("Tipo")
        estado_values = self.service.get_master_values("Estado")
        prioridad_values = self.service.get_master_values("Prioridad")

        self.area_combo.configure(values=area_values)
        self.tipo_combo.configure(values=tipo_values)
        self.estado_combo.configure(values=estado_values)
        self.prioridad_combo.configure(values=prioridad_values)
        self.action_area_combo.configure(values=["Todas", *area_values])

        if area_values:
            self.area_var.set(area_values[0])
        if tipo_values:
            self.tipo_var.set(tipo_values[0])
        if "Pendiente" in estado_values:
            self.estado_var.set("Pendiente")
        elif estado_values:
            self.estado_var.set(estado_values[0])
        if "Media" in prioridad_values:
            self.prioridad_var.set("Media")
        elif prioridad_values:
            self.prioridad_var.set(prioridad_values[0])

    def _open_settings(self) -> None:
        current = self.service.get_settings()

        def on_save(new_settings: AppSettings) -> None:
            self.service.save_settings(new_settings)
            self._apply_defaults(new_settings)
            self._refresh_database_button_state()

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
            fecha=self.date_entry.get_date().isoformat(),
        )
        note_id, msg = self.service.create_note(req)
        if note_id is None:
            messagebox.showinfo("Duplicado", msg)
        else:
            messagebox.showinfo("OK", msg)
            self.text_widget.delete("1.0", "end")
            self.title_var.set("")
        self.refresh_notes()
        self.refresh_actions()

    def _sync(self) -> None:
        threading.Thread(target=self._sync_worker, daemon=True).start()

    def _sync_worker(self) -> None:
        try:
            self.msg_queue.put(("status", "Sincronizando notas pendientes..."))
            sent, failed = self.service.sync_pending()
            self.msg_queue.put(("info", f"Sincronización completada. Enviadas: {sent}, Errores: {failed}"))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("error", str(exc)))

    def _create_notion_database(self) -> None:
        self.create_db_button.config(state="disabled")
        self.status_var.set("Creando base de datos en Notion...")
        threading.Thread(target=self._create_notion_database_worker, daemon=True).start()

    def _create_notion_database_worker(self) -> None:
        try:
            database_id = self.service.create_notion_database_from_config()
            self.msg_queue.put(("db_success", f"Base Notion lista. DATABASE_ID: {database_id}"))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("db_error", str(exc)))

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, msg = self.msg_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self.status_var.set(f"Error: {msg}")
                messagebox.showerror("Error", msg)
            elif kind == "db_error":
                self.status_var.set(f"Error al crear base: {msg}")
                self.create_db_button.config(state="normal")
                messagebox.showerror("Error", msg)
            elif kind == "db_success":
                self.status_var.set("Base Notion creada correctamente")
                self.create_db_button.config(state="disabled")
                messagebox.showinfo("Éxito", msg)
            elif kind == "status":
                self.status_var.set(msg)
            else:
                self.status_var.set(msg)
                messagebox.showinfo("Resultado", msg)
            self.refresh_notes()
            self.refresh_actions()
        self.after(150, self._poll_queue)

    def _refresh_database_button_state(self) -> None:
        database_id = self.service.get_setting("notion_database_id")
        if database_id:
            self.create_db_button.config(state="disabled")
            self.status_var.set("DATABASE_ID detectado en SQLite. Base lista para usar.")
        else:
            self.create_db_button.config(state="normal")

    def refresh_notes(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        for note in self.service.list_notes():
            self.tree.insert("", "end", iid=str(note.id), values=(note.id, note.title, note.status, note.last_error or "", note.notion_page_id or ""))

    def refresh_actions(self) -> None:
        for row in self.actions_tree.get_children():
            self.actions_tree.delete(row)

        area_filter = self.action_area_var.get().strip()
        area = None if area_filter in ("", "Todas") else area_filter

        try:
            actions = self.service.list_pending_actions(area)
            for action in actions:
                self.actions_tree.insert(
                    "",
                    "end",
                    iid=f"a{action.id}",
                    values=(action.id, action.area, action.description, action.status, action.note_id),
                )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron cargar acciones")
            self.status_var.set("Error al cargar acciones")

    def _mark_selected_action_done(self) -> None:
        selection = self.actions_tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona una acción.")
            return

        action_id = int(self.actions_tree.item(selection[0], "values")[0])
        try:
            self.service.mark_action_done(action_id)
            self.status_var.set(f"Acción {action_id} marcada como hecha")
            self.refresh_actions()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo marcar la acción id=%s como hecha", action_id)
            messagebox.showerror("Error", "No se pudo actualizar la acción.")

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
