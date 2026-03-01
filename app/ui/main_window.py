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
from app.ui.masters_dialog import MastersDialog
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
        self.pack(fill="both", expand=True)
        self.actions_data: list[tuple[int, str, str, str, int]] = []
        self.filtered_actions_data: list[tuple[int, str, str, str, int]] = []
        self.action_sort_state: dict[str, bool] = {}
        self.action_columns = ("id", "area", "description", "status", "note_id")
        self.action_column_titles = {
            "id": "ID",
            "area": "Área",
            "description": "Descripción",
            "status": "Estado",
            "note_id": "Nota asociada",
        }

        self._build_menu()
        self._build_form()
        self._build_sections()
        self._load_master_values()
        self._refresh_database_button_state()
        self.refresh_notes()
        self.refresh_actions()
        self.after(150, self._poll_queue)


    def _build_menu(self) -> None:
        menubar = tk.Menu(self.master)

        archivo = tk.Menu(menubar, tearoff=0)
        archivo.add_command(label="Salir", command=self.master.destroy)
        menubar.add_cascade(label="Archivo", menu=archivo)

        edicion = tk.Menu(menubar, tearoff=0)
        edicion.add_command(label="Refrescar notas", command=self.refresh_notes)
        menubar.add_cascade(label="Edición", menu=edicion)

        herramientas = tk.Menu(menubar, tearoff=0)
        herramientas.add_command(label="Configuración", command=self._open_settings)
        menubar.add_cascade(label="Herramientas", menu=herramientas)

        maestros = tk.Menu(menubar, tearoff=0)
        maestros.add_command(label="Gestionar Áreas", command=lambda: self._open_masters_dialog("Area"))
        maestros.add_command(label="Gestionar Tipos", command=lambda: self._open_masters_dialog("Tipo"))
        maestros.add_command(label="Gestionar Prioridades", command=lambda: self._open_masters_dialog("Prioridad"))
        maestros.add_command(label="Gestionar Orígenes", command=lambda: self._open_masters_dialog("Origen"))
        menubar.add_cascade(label="Maestros", menu=maestros)

        ia = tk.Menu(menubar, tearoff=0)
        ia.add_command(label="Sincronizar pendientes", command=self._sync)
        menubar.add_cascade(label="IA", menu=ia)

        self.master.config(menu=menubar)

    def _open_masters_dialog(self, category: str) -> None:
        MastersDialog(self.master, self.service, category, self._load_master_values)

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
        ttk.Button(toolbar, text="Marcar como hecha", command=self._mark_selected_action_done).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Refrescar", command=self.refresh_actions).pack(side="left", padx=6)

        filters_frame = ttk.Frame(actions_frame)
        filters_frame.pack(fill="x", padx=4, pady=(0, 4))

        self.action_filter_vars: dict[str, tk.StringVar] = {}
        self.action_filter_entries: dict[str, ttk.Entry] = {}

        for index, column in enumerate(self.action_columns):
            filters_frame.columnconfigure(index, weight=1)
            filter_var = tk.StringVar()
            self.action_filter_vars[column] = filter_var
            entry = ttk.Entry(filters_frame, textvariable=filter_var)
            entry.grid(row=0, column=index, padx=2, sticky="ew")
            entry.bind("<Return>", lambda _event: self.apply_filters())
            self.action_filter_entries[column] = entry

        filter_buttons = ttk.Frame(filters_frame)
        filter_buttons.grid(row=0, column=len(self.action_columns), padx=(8, 0), sticky="e")
        ttk.Button(filter_buttons, text="Filtrar", command=self.apply_filters).pack(side="left", padx=2)
        ttk.Button(filter_buttons, text="Limpiar filtros", command=self.clear_filters).pack(side="left", padx=2)

        self.actions_tree = ttk.Treeview(actions_frame, columns=self.action_columns, show="headings", height=12)
        self.actions_tree.heading("id", text="ID", command=lambda: self._on_action_column_click("id"))
        self.actions_tree.heading("area", text="Área", command=lambda: self._on_action_column_click("area"))
        self.actions_tree.heading("description", text="Descripción", command=lambda: self._on_action_column_click("description"))
        self.actions_tree.heading("status", text="Estado", command=lambda: self._on_action_column_click("status"))
        self.actions_tree.heading("note_id", text="Nota asociada", command=lambda: self._on_action_column_click("note_id"))
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
        try:
            actions = self.service.list_pending_actions()
            self.actions_data = [(action.id, action.area, action.description, action.status, action.note_id) for action in actions]
            self.apply_filters()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron cargar acciones")
            self.status_var.set("Error al cargar acciones")

    def _refresh_actions_tree(self, rows: list[tuple[int, str, str, str, int]]) -> None:
        for row in self.actions_tree.get_children():
            self.actions_tree.delete(row)

        for action in rows:
            self.actions_tree.insert("", "end", iid=f"a{action[0]}", values=action)

    def apply_filters(self) -> None:
        filtered_rows = self.actions_data
        for col_index, col_name in enumerate(self.action_columns):
            filter_text = self.action_filter_vars[col_name].get().strip().lower()
            if filter_text:
                filtered_rows = [
                    row for row in filtered_rows if filter_text in str(row[col_index]).lower()
                ]

        self.filtered_actions_data = list(filtered_rows)

        if self.action_sort_state:
            sorted_col = next(iter(self.action_sort_state))
            self.sort_column(sorted_col, self.action_sort_state[sorted_col], refresh_only=True)
            return

        self._refresh_actions_tree(self.filtered_actions_data)

    def clear_filters(self) -> None:
        for filter_var in self.action_filter_vars.values():
            filter_var.set("")
        self.apply_filters()

    def _on_action_column_click(self, col: str) -> None:
        if col in self.action_sort_state:
            reverse = not self.action_sort_state[col]
        else:
            reverse = False
        self.sort_column(col, reverse)

    def sort_column(self, col: str, reverse: bool, refresh_only: bool = False) -> None:
        col_index = self.action_columns.index(col)
        sorted_rows = sorted(
            self.filtered_actions_data,
            key=lambda row: (str(row[col_index]).lower() if isinstance(row[col_index], str) else row[col_index]),
            reverse=reverse,
        )
        self.filtered_actions_data = sorted_rows
        self._refresh_actions_tree(self.filtered_actions_data)

        if refresh_only:
            self._update_action_headers()
            return

        self.action_sort_state = {col: reverse}
        self._update_action_headers()

    def _update_action_headers(self) -> None:
        for column in self.action_columns:
            title = self.action_column_titles[column]
            if column in self.action_sort_state:
                arrow = "⬇️" if self.action_sort_state[column] else "⬆️"
                title = f"{title} {arrow}"
            self.actions_tree.heading(column, text=title, command=lambda c=column: self._on_action_column_click(c))

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
