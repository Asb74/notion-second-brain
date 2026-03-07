"""Main Tkinter window."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from tkcalendar import DateEntry

from app.core.calendar.google_calendar_client import (
    GoogleCalendarClient,
    crear_evento_google_calendar,
    get_calendar_service,
)
from app.core.models import AppSettings, NoteCreateRequest
from app.core.service import NoteService
from app.ui.excel_filter import ExcelTreeFilter
from app.ui.masters_dialog import MastersDialog
from app.ui.email_manager_window import EmailManagerWindow
from app.ui.calendar_manager_window import CalendarManagerWindow
from app.ui.settings_dialog import SettingsDialog
from app.ui.user_profile_window import UserProfileWindow

logger = logging.getLogger(__name__)

def generar_intervalos_15() -> list[str]:
    """Genera intervalos de 15 minutos para un día completo."""
    horas: list[str] = []
    for h in range(24):
        for m in [0, 15, 30, 45]:
            horas.append(f"{h:02d}:{m:02d}")
    return horas


def calcular_hora_fin(hora_inicio: str, duracion_minutos: int) -> str:
    """Calcula hora fin desde una hora de inicio y duración en minutos."""
    inicio = datetime.strptime(hora_inicio, "%H:%M")
    fin = inicio + timedelta(minutes=duracion_minutos)
    return fin.strftime("%H:%M")


def duracion_desde_etiqueta(etiqueta: str) -> int:
    """Convierte una etiqueta de duración (ej. "60 min") a minutos."""
    try:
        return int(str(etiqueta).strip().split()[0])
    except (TypeError, ValueError, IndexError):
        return 60


class MainWindow(ttk.Frame):
    """Primary app UI with note form and sync status list."""

    def __init__(
        self,
        master: tk.Tk,
        service: NoteService,
        db_connection: sqlite3.Connection | None = None,
        gmail_credentials_path: str = "secrets/gmail_credentials.json",
        gmail_token_path: str = "secrets/gmail_token.json",
    ):
        super().__init__(master, padding=10)
        self.master = master
        self.service = service
        self.db_connection = db_connection
        self.gmail_credentials_path = gmail_credentials_path
        self.gmail_token_path = gmail_token_path
        self._email_window: EmailManagerWindow | None = None
        self._profile_window: UserProfileWindow | None = None
        self._calendar_toplevel: tk.Toplevel | None = None
        self._calendar_window: CalendarManagerWindow | None = None
        self._calendar_client: GoogleCalendarClient | None = None
        self.calendar_service = None
        self.msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.status_var = tk.StringVar(value="Listo")
        self.pack(fill="both", expand=True)
        self.notes_data: list[tuple[int, str, str, str, str]] = []
        self.filtered_notes_data: list[tuple[int, str, str, str, str]] = []
        self._entry_filtered_notes_data: list[tuple[int, str, str, str, str]] = []
        self.note_columns = ("id", "title", "status", "error", "notion_page_id")
        self.note_column_titles = {
            "id": "ID",
            "title": "Título",
            "status": "Estado",
            "error": "Error",
            "notion_page_id": "Notion ID",
        }
        self.actions_data: list[tuple[int, str, str, str, int, str]] = []
        self.filtered_actions_data: list[tuple[int, str, str, str, int, str]] = []
        self.action_columns = ("id", "area", "description", "status", "note_id", "notion_page_id")
        self.action_column_titles = {
            "id": "ID",
            "area": "Área",
            "description": "Descripción",
            "status": "Estado",
            "note_id": "Nota asociada",
            "notion_page_id": "Notion ID",
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
        herramientas.add_command(label="Gestión de Emails", command=self._open_email_manager)
        herramientas.add_command(label="Agenda", command=self._open_calendar_manager)
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

    def _ensure_email_manager_window(self) -> EmailManagerWindow | None:
        if self.db_connection is None:
            messagebox.showerror("Error", "No hay conexión de base de datos disponible para emails.")
            return None

        if self._email_window is not None and self._email_window.winfo_exists():
            self._email_window.focus_set()
            return self._email_window

        try:
            from app.core.email.gmail_client import GmailClient

            credentials_path = Path(self.gmail_credentials_path)
            token_path = Path(self.gmail_token_path)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            gmail_client = GmailClient(str(credentials_path), str(token_path))
            self._email_window = EmailManagerWindow(self.master, self.service, self.db_connection, gmail_client)
            return self._email_window
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir la ventana de gestión de emails")
            messagebox.showerror("Error", f"No se pudo abrir la gestión de emails.\n\n{exc}")
            return None

    def _open_email_manager(self) -> None:
        self._ensure_email_manager_window()


    def _ensure_calendar_window(self) -> CalendarManagerWindow:
        if self._calendar_toplevel is not None and self._calendar_toplevel.winfo_exists() and self._calendar_window is not None:
            self._calendar_toplevel.deiconify()
            self._calendar_toplevel.lift()
            self._calendar_toplevel.focus_force()
            self._calendar_window.refresh_overview()
            return self._calendar_window

        toplevel = tk.Toplevel(self.master)
        toplevel.title("Agenda")
        toplevel.geometry("1120x760")
        toplevel.minsize(820, 480)
        calendar_window = CalendarManagerWindow(toplevel, self.service)
        calendar_window.pack(fill="both", expand=True)
        self._calendar_toplevel = toplevel
        self._calendar_window = calendar_window
        return calendar_window

    def _open_calendar_manager(self) -> None:
        try:
            self._ensure_calendar_window()
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir la agenda")
            messagebox.showerror("Error", f"No se pudo abrir la agenda.\n\n{exc}")

    def _process_completion_event(self, completion: dict[str, str | int] | None) -> None:
        if not completion:
            return

        gmail_id = str(completion.get("gmail_id", "")).strip()
        body = str(completion.get("body", "")).strip()

        if not gmail_id:
            return

        message = (
            "Has terminado todas las tareas asociadas a este email.\n"
            "¿Deseas preparar una respuesta?"
        )
        if not messagebox.askyesno("Email finalizado", message):
            return

        email_window = self._ensure_email_manager_window()
        if email_window is None:
            return

        found = email_window.select_email_by_gmail_id(gmail_id)
        if not found:
            return

        email_window.set_reply_body(body)
        email_window.focus_force()

    def _build_form(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))

        form = ttk.LabelFrame(self, text="Nueva nota")
        form.pack(fill="x", pady=5)

        self.source_var = tk.StringVar(value="manual")
        self.area_var = tk.StringVar()
        self.tipo_var = tk.StringVar()
        self.estado_var = tk.StringVar(value="Pendiente")
        self.prioridad_var = tk.StringVar(value="Media")
        self.title_var = tk.StringVar()

        ttk.Label(form, text="Título").grid(row=0, column=0, padx=6, pady=6, sticky="e")
        ttk.Entry(form, textvariable=self.title_var, width=40).grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        ttk.Label(form, text="Fuente").grid(row=0, column=2, padx=6, pady=6, sticky="e")
        ttk.Combobox(form, textvariable=self.source_var, values=["manual", "email_pasted"], state="readonly", width=15).grid(row=0, column=3, padx=6, pady=6, sticky="w")

        self.text_widget = tk.Text(form, height=10, width=100)
        self.text_widget.grid(row=1, column=0, columnspan=10, sticky="ew", padx=6, pady=6)

        ttk.Label(form, text="Área").grid(row=2, column=0, padx=6, pady=6, sticky="e")
        self.area_combo = ttk.Combobox(form, textvariable=self.area_var, state="readonly", width=15)
        self.area_combo.grid(row=2, column=1, padx=6, pady=6)

        ttk.Label(form, text="Tipo").grid(row=2, column=2, padx=6, pady=6, sticky="e")
        self.tipo_combo = ttk.Combobox(form, textvariable=self.tipo_var, state="readonly", width=15)
        self.tipo_combo.grid(row=2, column=3, padx=6, pady=6)
        self.tipo_combo.bind("<<ComboboxSelected>>", self._on_tipo_changed)

        ttk.Label(form, text="Estado").grid(row=2, column=4, padx=6, pady=6, sticky="e")
        self.estado_combo = ttk.Combobox(form, textvariable=self.estado_var, state="readonly", width=15)
        self.estado_combo.grid(row=2, column=5, padx=6, pady=6)

        ttk.Label(form, text="Prioridad").grid(row=2, column=6, padx=6, pady=6, sticky="e")
        self.prioridad_combo = ttk.Combobox(form, textvariable=self.prioridad_var, state="readonly", width=15)
        self.prioridad_combo.grid(row=2, column=7, padx=6, pady=6)

        ttk.Label(form, text="Fecha").grid(row=2, column=8, padx=6, pady=6, sticky="e")
        self.date_entry = DateEntry(form, width=15, date_pattern="yyyy-mm-dd")
        self.date_entry.grid(row=2, column=9, padx=6, pady=6)

        self.event_time_frame = ttk.Frame(form)
        self.event_time_frame.grid(row=3, column=0, columnspan=10, sticky="ew", padx=6, pady=(0, 6))
        ttk.Label(self.event_time_frame, text="Hora inicio").grid(row=0, column=0, padx=6, pady=2, sticky="e")
        self.hora_inicio_var = tk.StringVar()
        self.hora_inicio_combo = ttk.Combobox(
            self.event_time_frame,
            textvariable=self.hora_inicio_var,
            state="readonly",
            width=10,
            values=generar_intervalos_15(),
        )
        self.hora_inicio_combo.grid(row=0, column=1, padx=6, pady=2, sticky="w")

        self.hora_selector = tk.Listbox(self.event_time_frame, height=8, exportselection=False)
        for hora in generar_intervalos_15():
            self.hora_selector.insert(tk.END, hora)
        self.hora_selector.grid(row=1, column=0, columnspan=2, padx=6, pady=2, sticky="ew")
        self.hora_selector.bind("<<ListboxSelect>>", self._on_hora_inicio_selected)

        ttk.Label(self.event_time_frame, text="Duración").grid(row=0, column=2, padx=6, pady=2, sticky="e")
        self.duracion_var = tk.StringVar(value="60 min")
        self.duracion_combo = ttk.Combobox(
            self.event_time_frame,
            textvariable=self.duracion_var,
            state="readonly",
            width=10,
            values=["15 min", "30 min", "45 min", "60 min", "90 min", "120 min"],
        )
        self.duracion_combo.grid(row=0, column=3, padx=6, pady=2, sticky="w")
        self.duracion_combo.bind("<<ComboboxSelected>>", self._on_duracion_changed)

        ttk.Label(self.event_time_frame, text="Hora fin").grid(row=1, column=2, padx=6, pady=2, sticky="e")
        self.hora_fin_var = tk.StringVar()
        ttk.Label(self.event_time_frame, textvariable=self.hora_fin_var).grid(row=1, column=3, padx=6, pady=2, sticky="w")
        self.event_time_frame.columnconfigure(5, weight=1)

        for column in (1, 3):
            form.columnconfigure(column, weight=1)

        actions = ttk.Frame(form)
        actions.grid(row=4, column=0, columnspan=10, sticky="ew", pady=(4, 8), padx=6)
        action_buttons = [
            ("⚙ Perfil usuario", self._open_user_profile),
            ("Configuración", self._open_settings),
            ("Guardar", self._save_note),
            ("Enviar", self._sync),
            ("Reintentar", self._sync),
            ("Abrir en Notion", self._open_notion),
            ("Abrir evento", self._open_selected_note_google_event),
            ("Agenda", self._open_calendar_manager),
        ]
        for idx, (label, command) in enumerate(action_buttons):
            ttk.Button(actions, text=label, command=command, style="Toolbar.TButton").grid(
                row=0,
                column=idx * 2,
                sticky="ew",
                padx=(0, 6),
            )
            actions.columnconfigure(idx * 2, weight=1, uniform="main-actions")
            ttk.Separator(actions, orient="vertical").grid(row=0, column=idx * 2 + 1, sticky="ns", padx=(0, 6))

        self.create_db_button = ttk.Button(actions, text="Crear Base Notion", command=self._create_notion_database, style="Toolbar.TButton")
        create_idx = len(action_buttons) * 2
        self.create_db_button.grid(row=0, column=create_idx, sticky="ew")
        actions.columnconfigure(create_idx, weight=1, uniform="main-actions")
        self._toggle_event_time_fields()

    def _open_user_profile(self) -> None:
        if self.db_connection is None:
            messagebox.showerror("Error", "No hay conexión de base de datos disponible.")
            return
        if self._profile_window is not None and self._profile_window.winfo_exists():
            self._profile_window.focus_set()
            return
        self._profile_window = UserProfileWindow(self.master, self.db_connection)

    def _build_sections(self) -> None:
        sections = ttk.Notebook(self)
        sections.pack(fill="both", expand=True, pady=8)

        notes_frame = ttk.Frame(sections)
        actions_frame = ttk.Frame(sections)
        sections.add(notes_frame, text="Notas")
        sections.add(actions_frame, text="Acciones")

        self.tree = ttk.Treeview(notes_frame, columns=self.note_columns, show="headings", height=12)
        for c in self.note_columns:
            self.tree.heading(c, text=c)
        self.tree.column("id", width=40)
        self.tree.column("title", width=260)
        self.tree.column("status", width=90)
        self.tree.column("error", width=260)
        self.tree.column("notion_page_id", width=220)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._open_selected_note())

        self.notes_excel_filter = ExcelTreeFilter(
            master=self,
            tree=self.tree,
            columns=self.note_columns,
            column_titles=self.note_column_titles,
            get_rows=lambda: self._entry_filtered_notes_data,
            set_rows=self._set_notes_filtered_rows,
        )

        notes_toolbar = ttk.Frame(notes_frame)
        notes_toolbar.pack(fill="x", padx=6, pady=4)
        ttk.Button(notes_toolbar, text="Abrir nota", command=self._open_selected_note).pack(side="left", padx=6)
        ttk.Button(notes_toolbar, text="Limpiar filtros", command=self.notes_excel_filter.clear_all_filters).pack(side="left", padx=6)

        toolbar = ttk.Frame(actions_frame)
        toolbar.pack(fill="x", padx=4, pady=4)
        ttk.Button(toolbar, text="Marcar como hecha", command=self._mark_selected_action_done).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Finalizar seleccionadas", command=self._mark_selected_actions_done).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Refrescar", command=self.refresh_actions).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Abrir", command=self._open_selected_action).pack(side="left", padx=6)

        self.actions_tree = ttk.Treeview(
            actions_frame,
            columns=self.action_columns,
            show="headings",
            height=12,
            selectmode="extended",
        )
        self.actions_tree.heading("id", text="ID")
        self.actions_tree.heading("area", text="Área")
        self.actions_tree.heading("description", text="Descripción")
        self.actions_tree.heading("status", text="Estado")
        self.actions_tree.heading("note_id", text="Nota asociada")
        self.actions_tree.heading("notion_page_id", text="Notion ID")
        self.actions_tree.column("id", width=50)
        self.actions_tree.column("area", width=140)
        self.actions_tree.column("description", width=420)
        self.actions_tree.column("status", width=100)
        self.actions_tree.column("note_id", width=130)
        self.actions_tree.column("notion_page_id", width=220)
        self.actions_tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.actions_tree.bind("<Double-1>", lambda e: self._open_selected_action())

        self._entry_filtered_actions_data: list[tuple[int, str, str, str, int, str]] = []
        self.actions_excel_filter = ExcelTreeFilter(
            master=self,
            tree=self.actions_tree,
            columns=self.action_columns,
            column_titles=self.action_column_titles,
            get_rows=lambda: self._entry_filtered_actions_data,
            set_rows=self._set_actions_filtered_rows,
        )

        ttk.Button(toolbar, text="Limpiar filtros", command=self.actions_excel_filter.clear_all_filters).pack(side="left", padx=6)

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
        self._toggle_event_time_fields()
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
        self._toggle_event_time_fields()

    def _save_note(self) -> None:
        raw_text = self.text_widget.get("1.0", "end").strip()
        if not raw_text:
            messagebox.showwarning("Validación", "El texto de la nota es obligatorio.")
            return

        tipo = self.tipo_var.get().strip() or "Nota"
        hora_inicio = self.hora_inicio_var.get().strip() or None
        duracion = duracion_desde_etiqueta(self.duracion_var.get()) if tipo.lower() == "evento" else None
        hora_fin = calcular_hora_fin(hora_inicio, duracion) if tipo.lower() == "evento" and hora_inicio and duracion else None

        if tipo.lower() == "evento" and hora_inicio is None:
            messagebox.showwarning("Validación", "Selecciona una hora de inicio para el evento.")
            return

        req = NoteCreateRequest(
            title=self.title_var.get().strip(),
            raw_text=raw_text,
            source=self.source_var.get(),
            area=self.area_var.get().strip() or "General",
            tipo=tipo,
            estado=self.estado_var.get().strip() or "Pendiente",
            prioridad=self.prioridad_var.get().strip() or "Media",
            fecha=self.date_entry.get_date().isoformat(),
            hora_inicio=hora_inicio,
            duracion=duracion,
            hora_fin=hora_fin,
        )
        note_id, msg = self.service.create_note(req)
        if note_id is None:
            messagebox.showinfo("Duplicado", msg)
        else:
            if tipo.lower() == "evento" and hora_inicio:
                event_data = self._create_google_calendar_event(
                    titulo=req.title or raw_text.split("\n", 1)[0][:120] or "Sin título",
                    descripcion=raw_text,
                    fecha=req.fecha,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                )
                if event_data:
                    self.service.update_note_google_event_data(
                        note_id,
                        str(event_data.get("id") or ""),
                        str(event_data.get("htmlLink") or ""),
                    )
            messagebox.showinfo("OK", msg)
            self.text_widget.delete("1.0", "end")
            self.title_var.set("")
            self.hora_inicio_var.set("")
            self.duracion_var.set("60 min")
            self.hora_fin_var.set("")
        self.refresh_notes()
        self.refresh_actions()

    def _on_tipo_changed(self, _event: tk.Event | None = None) -> None:
        self._toggle_event_time_fields()

    def _toggle_event_time_fields(self) -> None:
        tipo = self.tipo_var.get().strip().lower()
        if tipo == "evento":
            self.event_time_frame.grid()
        else:
            self.event_time_frame.grid_remove()
            self.hora_inicio_var.set("")
            self.duracion_var.set("60 min")
            self.hora_fin_var.set("")


    def _on_hora_inicio_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.hora_selector.curselection()
        if not selection:
            return
        value = str(self.hora_selector.get(selection[0]))
        self.hora_inicio_var.set(value)
        if self.tipo_var.get().strip().lower() == "evento":
            self.hora_fin_var.set(calcular_hora_fin(value, duracion_desde_etiqueta(self.duracion_var.get())))


    def _on_duracion_changed(self, _event: tk.Event | None = None) -> None:
        hora_inicio = self.hora_inicio_var.get().strip()
        if not hora_inicio:
            return
        self.hora_fin_var.set(calcular_hora_fin(hora_inicio, duracion_desde_etiqueta(self.duracion_var.get())))


    def _create_google_calendar_event(self, titulo: str, descripcion: str, fecha: str, hora_inicio: str, hora_fin: str | None) -> dict | None:
        try:
            service = self._get_calendar_service()
            if service is None:
                return None
            return crear_evento_google_calendar(service, titulo, descripcion, fecha, hora_inicio, hora_fin)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo crear el evento en Google Calendar")
            messagebox.showwarning("Google Calendar", "No se pudo crear el evento en Google Calendar")
            return None

    def _get_calendar_service(self):
        if self.calendar_service is not None:
            return self.calendar_service

        token_path = Path(r"C:\notion-second-brain\secrets\calendar_token.json")
        if not token_path.exists():
            token_path = Path("secrets/calendar_token.json")

        try:
            if token_path.exists():
                self.calendar_service = get_calendar_service(str(token_path))
                return self.calendar_service
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo inicializar servicio directo de Google Calendar")

        client = self._get_calendar_client()
        if client is not None:
            self.calendar_service = client.service
        return self.calendar_service

    def _get_calendar_client(self) -> GoogleCalendarClient | None:
        if self._calendar_client is not None:
            return self._calendar_client

        credentials_path = Path(r"C:\notion-second-brain\secrets\calendar_credentials.json")
        token_path = Path(r"C:\notion-second-brain\secrets\calendar_token.json")

        if not credentials_path.exists():
            credentials_path = Path("secrets/calendar_credentials.json")
        if not token_path.parent.exists():
            token_path = Path("secrets/calendar_token.json")

        try:
            self._calendar_client = GoogleCalendarClient(str(credentials_path), str(token_path))
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo inicializar Google Calendar")
            self._calendar_client = None
        return self._calendar_client

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
        try:
            notes = self.service.list_notes()
            self.notes_data = [
                (note.id, note.title, note.status, note.last_error or "", note.notion_page_id or "")
                for note in notes
            ]
            self.apply_note_filters()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron cargar notas")
            self.status_var.set("Error al cargar notas")

    def apply_note_filters(self) -> None:
        self._entry_filtered_notes_data = list(self.notes_data)
        self.notes_excel_filter.apply()

    def _set_notes_filtered_rows(self, rows: list[tuple[int, str, str, str, str]]) -> None:
        self.filtered_notes_data = rows
        self._refresh_notes_tree(rows)

    def _refresh_notes_tree(self, rows: list[tuple[int, str, str, str, str]]) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)

        for note in rows:
            self.tree.insert("", "end", iid=str(note[0]), values=note)

    def refresh_actions(self) -> None:
        try:
            actions = self.service.list_pending_actions()
            self.actions_data = [
                (
                    action.id,
                    action.area,
                    action.description,
                    action.status,
                    action.note_id,
                    action.notion_page_id or "",
                )
                for action in actions
            ]
            self.apply_filters()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron cargar acciones")
            self.status_var.set("Error al cargar acciones")


    def apply_filters(self) -> None:
        # Orden de aplicación acordado:
        # 1) filtros por Entry (contains), cuando existan en la vista
        # 2) filtros Excel (lista/condición + ordenación tipada)
        self._entry_filtered_actions_data = list(self.actions_data)
        self.actions_excel_filter.apply()

    def _set_actions_filtered_rows(self, rows: list[tuple[int, str, str, str, int, str]]) -> None:
        self.filtered_actions_data = rows
        self._refresh_actions_tree(rows)

    def _action_row_to_display(self, row: tuple[int, str, str, str, int, str]) -> dict[str, str]:
        return {
            "id": str(row[0]),
            "area": row[1] or "",
            "description": row[2] or "",
            "status": row[3] or "",
            "note_id": str(row[4]) if row[4] is not None else "",
            "notion_page_id": row[5] or "",
        }

    def _refresh_actions_tree(self, rows: list[tuple[int, str, str, str, int, str]]) -> None:
        for row in self.actions_tree.get_children():
            self.actions_tree.delete(row)

        for action in rows:
            self.actions_tree.insert("", "end", iid=f"a{action[0]}", values=action)

    def _mark_selected_action_done(self) -> None:
        selection = self.actions_tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona una acción.")
            return

        action_id = int(self.actions_tree.item(selection[0], "values")[0])
        try:
            completion = self.service.mark_action_done(action_id)
            self._process_completion_event(completion)
            self.status_var.set(f"Acción {action_id} marcada como hecha")
            self.refresh_actions()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo marcar la acción id=%s como hecha", action_id)
            messagebox.showerror("Error", "No se pudo actualizar la acción.")

    def _mark_selected_actions_done(self) -> None:
        selection = self.actions_tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona al menos una acción.")
            return

        action_ids = [int(self.actions_tree.item(iid, "values")[0]) for iid in selection]

        try:
            events = self.service.mark_actions_done(action_ids)
            if events:
                for event in events:
                    self._process_completion_event(event)
            self.status_var.set(f"Acciones finalizadas: {len(action_ids)}")
            self.refresh_actions()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron finalizar las acciones seleccionadas: %s", action_ids)
            messagebox.showerror("Error", "No se pudieron finalizar las acciones seleccionadas.")

    def _open_notion(self) -> None:
        self._open_selected_note()

    def _open_notion_page(self, notion_page_id: str) -> None:
        if not notion_page_id:
            messagebox.showwarning("Atención", "No hay Notion ID asociado.")
            return

        url = f"https://www.notion.so/{notion_page_id.replace('-', '')}"
        webbrowser.open(url)

    def _open_selected_note_google_event(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona una nota.")
            return

        values = self.tree.item(selection[0], "values")
        note_id = int(values[0])
        note = self.service.get_note_by_id(note_id)
        if note and note.google_calendar_link:
            webbrowser.open(note.google_calendar_link)
            return

        messagebox.showwarning("Atención", "La nota seleccionada no tiene evento de Google Calendar asociado.")

    def _open_selected_note(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona una nota.")
            return

        values = self.tree.item(selection[0], "values")
        notion_page_id = values[4]
        self._open_notion_page(notion_page_id)

    def _open_selected_action(self) -> None:
        selection = self.actions_tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona una acción.")
            return

        values = self.actions_tree.item(selection[0], "values")
        action_notion_id = values[5]
        note_id = values[4]

        if action_notion_id:
            self._open_notion_page(action_notion_id)
            return

        if note_id:
            note = self.service.get_note_by_id(int(note_id))
            if note and note.notion_page_id:
                self._open_notion_page(note.notion_page_id)
                return

        messagebox.showwarning("Atención", "No hay página Notion asociada.")
