"""Calendar agenda view for Google Calendar events and dated notes."""

from __future__ import annotations

import calendar
import logging
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from tkcalendar import DateEntry
from typing import Any, Callable

from app.core.calendar.google_calendar_client import GoogleCalendarClient
from app.core.models import Action, Note
from app.core.service import NoteService
from app.persistence.calendar_repository import CalendarRepository
from app.ui.app_icons import apply_app_icon
from app.ui.dictation_widgets import attach_dictation
from app.ui.excel_filter import ExcelTreeFilter

logger = logging.getLogger(__name__)

NOTE_PENDING_COLOR = "#4F81BD"
NOTE_DONE_COLOR = "#D9D9D9"
EMAIL_COLOR = "#70AD47"
EVENT_COLOR = "#ED7D31"
ACTION_COLOR = "#FFC000"
STATUS_OPTIONS = ["Pendiente", "En curso", "Finalizado", "Cancelado", "Completado"]


def _sanitize_tk_color(color: str | None, fallback: str = "#000000") -> str:
    """Return a Tkinter-safe color value for known invalid system color aliases."""
    value = str(color or "").strip()
    if not value:
        return fallback
    if value.lower() == "windowtext":
        return fallback
    return value


class CalendarManagerWindow(ttk.Frame):
    """CRM-like agenda frame with overview on top and details below."""

    TYPE_LABELS = {
        "NOTE": "📌 nota",
        "ACTION": "✔ acción",
        "EMAIL": "📧 email",
        "EVENT": "📅 evento",
    }

    TYPE_COLORS = {
        "NOTE": NOTE_PENDING_COLOR,
        "ACTION": ACTION_COLOR,
        "EMAIL": EMAIL_COLOR,
        "EVENT": EVENT_COLOR,
    }

    def __init__(self, master: tk.Misc, note_service: NoteService, calendar_repo: CalendarRepository | None = None):
        super().__init__(master, padding=10)
        self.note_service = note_service
        apply_app_icon(self.winfo_toplevel())
        self.calendar_client: GoogleCalendarClient | None = None
        self.calendar_repo = calendar_repo
        self._calendar_filter_vars: dict[str, tk.BooleanVar] = {}
        self._type_filter_vars: dict[str, tk.BooleanVar] = {
            "NOTE": tk.BooleanVar(value=True),
            "ACTION": tk.BooleanVar(value=True),
            "EMAIL": tk.BooleanVar(value=True),
            "EVENT": tk.BooleanVar(value=True),
        }
        self._calendar_color_swatches: list[tk.Frame] = []
        self.current_date = date.today()
        self.current_month = self.current_date.replace(day=1)
        self.view_mode = "month"
        self.calendar_events: list[dict] = []
        self.notes_with_dates: list[Note] = []
        self.actions: list[Action] = []
        self._label_metadata: dict[str, dict[str, str | int]] = {}
        self._events_by_day: dict[date, list[dict[str, str | int]]] = {}
        self._overview_records: dict[str, dict[str, str | int]] = {}
        self._all_rows: list[dict[str, str]] = []
        self._action_vars: dict[int, tk.BooleanVar] = {}
        self._selected_record: dict[str, str | int] | None = None
        self._calendar_mousewheel_bound = False
        self.open_email_manager_callback: Callable[[], Any] | None = None
        self.email_completion_callback: Callable[[dict[str, str | int] | None], None] | None = None
        self._attachments_by_name: dict[str, dict[str, Any]] = {}
        self._inline_action_row: ttk.Frame | None = None
        self._inline_action_entry: ttk.Entry | None = None
        self._inline_action_saving = False
        self._inline_title_entry: ttk.Entry | None = None
        self._inline_title_text: tk.Text | None = None
        self._inline_title_saving = False
        self._inline_status_widget: ttk.Combobox | None = None
        self._inline_status_saving = False
        self._inline_date_widget: DateEntry | None = None
        self._inline_date_saving = False
        self._inline_time_widget: ttk.Combobox | None = None
        self._inline_time_saving = False
        self.dragged_entry: dict[str, str | int] | None = None
        self.drag_origin_day: date | None = None
        self._dragged_widget: tk.Widget | None = None
        self._drag_target_day: date | None = None
        self._drag_hover_column: tk.Widget | None = None
        self._week_day_columns: dict[str, tk.Widget] = {}
        self.google_auth_status_var = tk.StringVar(value="")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))
        style.configure("CalendarHeader.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("Weekday.TLabel", anchor="center", font=("TkDefaultFont", 9, "bold"))
        style.configure("DayNumber.TLabel", font=("TkDefaultFont", 9, "bold"))
        style.configure("Item.TLabel", foreground=_sanitize_tk_color("#1f2937"))
        style.configure("MoreItems.TLabel", foreground=_sanitize_tk_color("#4b5563"))
        style.configure("Time.TLabel", foreground=_sanitize_tk_color("#4b5563"))

        self._build_menu_bar()
        self._build_toolbar()
        self._build_filter_bar()
        self._build_legend()
        self._build_layout()
        self._initialize_client()
        self.refresh_calendar()

    def _is_email_backed_record(self, record: dict[str, str | int]) -> bool:
        if str(record.get("kind") or "") == "EMAIL":
            return True
        return bool(str(record.get("gmail_id") or "").strip() or str(record.get("source_id") or "").strip())

    def _note_id_for_record(self, record: dict[str, str | int]) -> int:
        return int(record.get("note_id") or record.get("id") or 0)

    def _enrich_with_email_fields(self, record: dict[str, str | int], note: Note) -> None:
        if note.source != "email_pasted":
            return

        gmail_id = (note.source_id or "").strip()
        record["tipo"] = "email"
        record["gmail_id"] = gmail_id
        record["source_id"] = gmail_id
        record["thread_id"] = ""
        record["remitente"] = ""
        record["asunto"] = note.title or ""
        record["adjuntos"] = []

        email_window = self._open_email_manager()
        if email_window is not None and gmail_id:
            metadata = getattr(email_window, "get_email_metadata", lambda _gmail_id: {})(gmail_id)
            record["thread_id"] = str(metadata.get("thread_id") or "")
            record["remitente"] = str(metadata.get("sender") or "")
            record["asunto"] = str(metadata.get("subject") or note.title or "")
            record["adjuntos"] = getattr(email_window, "get_email_attachments", lambda _gmail_id: [])(gmail_id)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 8))

        nav_group = ttk.Frame(toolbar)
        nav_group.grid(row=0, column=0, sticky="w")
        ttk.Button(nav_group, text="Anterior", command=self._go_previous_month, style="Toolbar.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(nav_group, text="Hoy", command=self._go_today, style="Toolbar.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(nav_group, text="Siguiente", command=self._go_next_month, style="Toolbar.TButton").pack(side="left")

        ttk.Separator(toolbar, orient="vertical").grid(row=0, column=1, sticky="ns", padx=6)
        view_group = ttk.Frame(toolbar)
        view_group.grid(row=0, column=2, sticky="w")
        ttk.Button(view_group, text="Día", command=self._set_view_day, style="Toolbar.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(view_group, text="Semana", command=self._set_view_week, style="Toolbar.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(view_group, text="Mes", command=self._set_view_month, style="Toolbar.TButton").pack(side="left")

        ttk.Separator(toolbar, orient="vertical").grid(row=0, column=3, sticky="ns", padx=6)
        mode_group = ttk.Frame(toolbar)
        mode_group.grid(row=0, column=4, sticky="w")
        ttk.Button(mode_group, text="Lista", command=self._show_list_overview, style="Toolbar.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(mode_group, text="Calendario", command=self._show_calendar_overview, style="Toolbar.TButton").pack(side="left")

        ttk.Separator(toolbar, orient="vertical").grid(row=0, column=5, sticky="ns", padx=6)
        ttk.Button(toolbar, text="Actualizar", command=self.refresh_calendar, style="Toolbar.TButton").grid(row=0, column=6, padx=4)

        self.month_label = ttk.Label(toolbar, text="", style="CalendarHeader.TLabel")
        self.month_label.grid(row=0, column=7, sticky="ew", padx=8)
        ttk.Label(toolbar, textvariable=self.google_auth_status_var, anchor="e").grid(row=0, column=8, sticky="e", padx=(8, 0))

        toolbar.columnconfigure(7, weight=1)

    def _build_menu_bar(self) -> None:
        """Menú superior para igualar la arquitectura UX de Emails."""
        menubar = tk.Menu(self.winfo_toplevel())

        archivo = tk.Menu(menubar, tearoff=0)
        archivo.add_command(label="Actualizar", command=self.refresh_calendar)
        archivo.add_command(label="Hoy", command=self._go_today)
        archivo.add_separator()
        archivo.add_command(label="Cerrar", command=self.winfo_toplevel().destroy)
        menubar.add_cascade(label="Archivo", menu=archivo)

        vista = tk.Menu(menubar, tearoff=0)
        vista.add_command(label="Lista", command=self._show_list_overview)
        vista.add_command(label="Calendario", command=self._show_calendar_overview)
        vista.add_separator()
        vista.add_command(label="Día", command=self._set_view_day)
        vista.add_command(label="Semana", command=self._set_view_week)
        vista.add_command(label="Mes", command=self._set_view_month)
        menubar.add_cascade(label="Vista", menu=vista)

        self.winfo_toplevel().config(menu=menubar)

    def _build_filter_bar(self) -> None:
        self.filters_frame = ttk.LabelFrame(self, text="Filtros")
        self.filters_frame.pack(fill="x", pady=(0, 8))
        self.calendar_filters_frame = ttk.LabelFrame(self.filters_frame, text="Calendarios")
        self.calendar_filters_frame.pack(fill="x", padx=6, pady=(6, 4))

        types_frame = ttk.LabelFrame(self.filters_frame, text="Tipos")
        types_frame.pack(fill="x", padx=6, pady=(0, 6))
        for idx, (kind, var) in enumerate(self._type_filter_vars.items()):
            ttk.Checkbutton(types_frame, text=self.TYPE_LABELS[kind], variable=var, command=self._apply_global_search).grid(
                row=0,
                column=idx,
                sticky="w",
                padx=(0, 12),
                pady=4,
            )

    def _on_google_auth_event(self, event_name: str) -> None:
        if event_name == "reauthentication_required":
            messagebox.showinfo(
                "Reconectando con Google",
                "La conexión con Google ha expirado.\n"
                "Se abrirá una ventana del navegador para volver a conectar la cuenta.\n\n"
                "Esto solo tarda unos segundos.",
            )
            return

        if event_name == "reauthentication_started":
            self.google_auth_status_var.set("Reconectando con Google...")
            self.update_idletasks()
            return

        if event_name == "reauthentication_succeeded":
            self.google_auth_status_var.set("Conexión con Google restablecida correctamente.")
            self.after(4000, lambda: self.google_auth_status_var.set(""))

    def _build_calendar_filters(self) -> None:
        self.calendar_filters_frame = ttk.LabelFrame(self, text="Calendarios")
        self.calendar_filters_frame.pack(fill="x", pady=(0, 8))

    def _refresh_calendar_filters(self) -> None:
        for child in self.calendar_filters_frame.winfo_children():
            child.destroy()

        if self.calendar_repo is None:
            ttk.Label(self.calendar_filters_frame, text="Sin repositorio de calendarios disponible.").pack(anchor="w", padx=6, pady=4)
            return

        calendars = self.calendar_repo.list_calendars()
        if not calendars:
            ttk.Label(self.calendar_filters_frame, text="No hay calendarios sincronizados.").pack(anchor="w", padx=6, pady=4)
            return

        for idx, calendar_row in enumerate(calendars):
            calendar_id = str(calendar_row["google_calendar_id"])
            var = self._calendar_filter_vars.get(calendar_id)
            if var is None:
                var = tk.BooleanVar(value=bool(calendar_row["selected"]))
                self._calendar_filter_vars[calendar_id] = var
            else:
                var.set(bool(calendar_row["selected"]))

            row = ttk.Frame(self.calendar_filters_frame)
            row.grid(row=idx // 4, column=idx % 4, sticky="w", padx=(0, 12), pady=2)

            tk.Frame(
                row,
                width=12,
                height=12,
                bg=_sanitize_tk_color(str(calendar_row["background_color"]), fallback=EVENT_COLOR),
                bd=1,
                relief="solid",
            ).pack(side="left", padx=(0, 4))

            ttk.Checkbutton(
                row,
                text=str(calendar_row["name"]),
                variable=var,
                command=lambda cid=calendar_id, v=var: self._on_toggle_calendar_filter(cid, v),
            ).pack(side="left")

    def _on_toggle_calendar_filter(self, google_calendar_id: str, var: tk.BooleanVar) -> None:
        if self.calendar_repo is None:
            return
        self.calendar_repo.set_calendar_selected(google_calendar_id, 1 if var.get() else 0)
        self.refresh_calendar()

    def _build_legend(self) -> None:
        legend_frame = ttk.Frame(self)
        legend_frame.pack(fill="x", pady=(0, 8))

        legend_items = [
            ("📌 Nota pendiente", NOTE_PENDING_COLOR),
            ("✔ Acción", ACTION_COLOR),
            ("📧 Email", EMAIL_COLOR),
            ("📅 Evento", EVENT_COLOR),
            ("✓ Completado", NOTE_DONE_COLOR),
        ]

        for index, (label_text, color) in enumerate(legend_items):
            item = ttk.Frame(legend_frame)
            item.grid(row=0, column=index, padx=(0, 10), sticky="w")

            tk.Frame(item, width=14, height=14, bg=_sanitize_tk_color(color, fallback=EVENT_COLOR), bd=1, relief="solid").pack(side="left", padx=(0, 5))
            ttk.Label(item, text=label_text).pack(side="left")

        legend_frame.columnconfigure(len(legend_items), weight=1)
        self.week_label = ttk.Label(legend_frame, text="", font=("Segoe UI", 10, "bold"), foreground="#2f2f2f")
        self.week_label.grid(row=0, column=len(legend_items) + 1, sticky="e", padx=(10, 0))

    def _update_week_label(self, reference_date: date) -> None:
        week_number = reference_date.isocalendar().week
        self.week_label.config(text=f"SEMANA {week_number}")

    def _build_layout(self) -> None:
        self.crm_paned = ttk.PanedWindow(self, orient="vertical")
        self.crm_paned.pack(fill="both", expand=True)

        self.overview_panel = ttk.Frame(self.crm_paned)
        self.detail_panel = ttk.Frame(self.crm_paned, padding=8)
        self.crm_paned.add(self.overview_panel, weight=3)
        self.crm_paned.add(self.detail_panel, weight=2)
        self.bind("<Configure>", self._set_initial_panel_sizes, add="+")

        self.overview_stack = ttk.Frame(self.overview_panel)
        self.overview_stack.pack(fill="both", expand=True)

        self.list_frame = ttk.Frame(self.overview_stack)
        self.calendar_overview_frame = ttk.Frame(self.overview_stack)
        for frame in (self.list_frame, self.calendar_overview_frame):
            frame.grid(row=0, column=0, sticky="nsew")
        self.overview_stack.rowconfigure(0, weight=1)
        self.overview_stack.columnconfigure(0, weight=1)

        self._build_overview_list()

        self.calendar_scroll_container = ttk.Frame(self.calendar_overview_frame)
        self.calendar_scroll_container.pack(fill="both", expand=True)

        self.calendar_canvas = tk.Canvas(self.calendar_scroll_container, highlightthickness=0)
        self.calendar_scrollbar = ttk.Scrollbar(self.calendar_scroll_container, orient="vertical", command=self.calendar_canvas.yview)
        self.calendar_hscrollbar = ttk.Scrollbar(self.calendar_scroll_container, orient="horizontal", command=self.calendar_canvas.xview)
        self.calendar_canvas.configure(yscrollcommand=self.calendar_scrollbar.set, xscrollcommand=self.calendar_hscrollbar.set)

        self.calendar_scrollbar.pack(side="right", fill="y")
        self.calendar_hscrollbar.pack(side="bottom", fill="x")
        self.calendar_canvas.pack(side="left", fill="both", expand=True)

        self.calendar_frame = ttk.Frame(self.calendar_canvas)
        self.calendar_canvas_window = self.calendar_canvas.create_window((0, 0), window=self.calendar_frame, anchor="nw")
        self.calendar_frame.bind("<Configure>", self._on_calendar_configure)
        self.calendar_canvas.bind("<Configure>", self._on_calendar_canvas_configure)
        self.calendar_canvas.bind("<Enter>", self._bind_calendar_mousewheel)
        self.calendar_canvas.bind("<Leave>", self._unbind_calendar_mousewheel)

        self._build_detail_panel()
        self._show_calendar_overview()

    def _build_overview_list(self) -> None:
        toolbar = ttk.Frame(self.list_frame)
        toolbar.pack(fill="x", pady=(0, 6))

        self.search_var = tk.StringVar()
        ttk.Label(toolbar, text="Buscar:").pack(side="left")
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left", padx=4)
        self.search_entry.bind("<KeyRelease>", self._apply_global_search)
        ttk.Button(toolbar, text="Limpiar filtros", command=self._clear_list_filters, style="Toolbar.TButton").pack(side="left", padx=(6, 0))

        self.columns = ("id", "title", "tipo", "estado", "fecha")
        self.column_titles = {
            "id": "ID",
            "title": "Título",
            "tipo": "Tipo",
            "estado": "Estado",
            "fecha": "Fecha",
        }
        self.tree = ttk.Treeview(self.list_frame, columns=self.columns, show="headings", height=16)
        for col in self.columns:
            self.tree.heading(col, text=self.column_titles.get(col, col))
        self.tree.column("id", width=120, anchor="w")
        self.tree.column("title", width=440, anchor="w")
        self.tree.column("tipo", width=140, anchor="w")
        self.tree.column("estado", width=130, anchor="center")
        self.tree.column("fecha", width=140, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_overview_select)
        self.tree.bind("<Double-1>", self._on_overview_double_click)

        self.tree.tag_configure("NOTE_PENDING", background=_sanitize_tk_color(NOTE_PENDING_COLOR, fallback=NOTE_PENDING_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("ACTION_PENDING", background=_sanitize_tk_color(ACTION_COLOR, fallback=ACTION_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("EMAIL_PENDING", background=_sanitize_tk_color(EMAIL_COLOR, fallback=EMAIL_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("EVENT_PENDING", background=_sanitize_tk_color(EVENT_COLOR, fallback=EVENT_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("NOTE_DONE", background=_sanitize_tk_color(NOTE_DONE_COLOR, fallback=NOTE_DONE_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("ACTION_DONE", background=_sanitize_tk_color(NOTE_DONE_COLOR, fallback=NOTE_DONE_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("EMAIL_DONE", background=_sanitize_tk_color(NOTE_DONE_COLOR, fallback=NOTE_DONE_COLOR), foreground=_sanitize_tk_color("#000000"))
        self.tree.tag_configure("EVENT_DONE", background=_sanitize_tk_color(NOTE_DONE_COLOR, fallback=NOTE_DONE_COLOR), foreground=_sanitize_tk_color("#000000"))

        self.excel_filter = ExcelTreeFilter(
            master=self,
            tree=self.tree,
            columns=self.columns,
            column_titles=self.column_titles,
            get_rows=lambda: self._rows_for_excel_filter(),
            set_rows=self._set_filtered_rows,
        )

        self.search_count_var = tk.StringVar(value="Resultados encontrados: 0")
        self.search_count_label = ttk.Label(self.list_frame, textvariable=self.search_count_var)
        self.search_count_label.pack(anchor="w", padx=10, pady=(2, 6))

    def _set_initial_panel_sizes(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "_initial_sash_set", False):
            return
        total_height = self.crm_paned.winfo_height()
        if total_height <= 1:
            return
        self.crm_paned.sashpos(0, int(total_height * 0.6))
        self._initial_sash_set = True

    def _build_detail_panel(self) -> None:
        self.detail_title_var = tk.StringVar(value="Selecciona un registro")
        self.detail_type_var = tk.StringVar(value="-")
        self.detail_status_var = tk.StringVar(value="-")
        self.detail_date_var = tk.StringVar(value="-")
        self.detail_time_var = tk.StringVar(value="-")
        self.detail_calendar_var = tk.StringVar(value="-")

        # Arquitectura tipo Emails: detalle en notebook con pestañas dedicadas.
        self.detail_notebook = ttk.Notebook(self.detail_panel)
        self.detail_notebook.pack(fill="both", expand=True)

        self.detail_tab = ttk.Frame(self.detail_notebook, padding=8)
        self.actions_tab = ttk.Frame(self.detail_notebook, padding=8)
        self.attachments_tab = ttk.Frame(self.detail_notebook, padding=8)
        self.notes_tab = ttk.Frame(self.detail_notebook, padding=8)
        self.detail_notebook.add(self.detail_tab, text="Detalle")
        self.detail_notebook.add(self.actions_tab, text="Acciones")
        self.detail_notebook.add(self.attachments_tab, text="Adjuntos")
        self.detail_notebook.add(self.notes_tab, text="Notas")

        self.detail_content = self.detail_tab
        self.detail_title_label = ttk.Label(
            self.detail_content,
            textvariable=self.detail_title_var,
            style="CalendarHeader.TLabel",
            font=("TkDefaultFont", 13, "bold"),
        )
        self.detail_title_label.pack(anchor="w", fill="x")
        self.detail_title_label.bind("<Double-Button-1>", self._start_inline_title_edit)

        self.detail_metadata = ttk.Frame(self.detail_content)
        self.detail_metadata.pack(fill="x", pady=(6, 8))
        ttk.Label(self.detail_metadata, text="Tipo:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(self.detail_metadata, textvariable=self.detail_type_var).grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Label(self.detail_metadata, text="Estado:").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.detail_status_label = ttk.Label(self.detail_metadata, textvariable=self.detail_status_var)
        self.detail_status_label.grid(row=0, column=3, sticky="w", padx=(0, 20))
        self.detail_status_label.bind("<Double-Button-1>", self._start_inline_status_edit)
        ttk.Label(self.detail_metadata, text="Fecha:").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.detail_date_label = ttk.Label(self.detail_metadata, textvariable=self.detail_date_var)
        self.detail_date_label.grid(row=0, column=5, sticky="w", padx=(0, 20))
        self.detail_date_label.bind("<Double-Button-1>", self._start_inline_date_edit)
        ttk.Label(self.detail_metadata, text="Hora:").grid(row=0, column=6, sticky="w", padx=(0, 6))
        self.detail_time_label = ttk.Label(self.detail_metadata, textvariable=self.detail_time_var)
        self.detail_time_label.grid(row=0, column=7, sticky="w", padx=(0, 20))
        self.detail_time_label.bind("<Double-Button-1>", self._start_inline_time_edit)
        ttk.Label(self.detail_metadata, text="Calendario:").grid(row=0, column=8, sticky="w", padx=(0, 6))
        self.detail_calendar_label = ttk.Label(self.detail_metadata, textvariable=self.detail_calendar_var)
        self.detail_calendar_label.grid(row=0, column=9, sticky="w")

        self.content_text = tk.Text(self.detail_content, height=10, wrap="word")
        self.content_text.pack(fill="both", expand=True)
        self.content_dictation_controls = attach_dictation(self.content_text, self.detail_content)
        self.content_dictation_controls.pack(anchor="w", pady=(4, 0))

        self.detail_context_buttons = ttk.Frame(self.detail_content)
        self.detail_context_buttons.pack(fill="x", pady=(6, 0))

        self.associated_actions_frame = ttk.LabelFrame(self.actions_tab, text="Acciones asociadas")
        self.associated_actions_frame.pack(fill="both", expand=True, pady=(0, 6))
        self.actions_context_buttons = ttk.Frame(self.actions_tab)
        self.actions_context_buttons.pack(fill="x")

        self.attachments_frame = ttk.LabelFrame(self.attachments_tab, text="Archivos adjuntos")
        self.attachments_frame.pack(fill="both", expand=True, pady=(0, 6))
        self.attachments_list = tk.Listbox(self.attachments_frame, height=5)
        self.attachments_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.attachments_list.bind("<Double-Button-1>", self._on_attachment_open)
        self.attachments_context_buttons = ttk.Frame(self.attachments_tab)
        self.attachments_context_buttons.pack(fill="x")

        ttk.Label(self.notes_tab, text="Espacio para notas futuras.").pack(anchor="w")

    def _update_detail_scrollregion(self, _event: tk.Event | None = None) -> None:
        return

    def _resize_detail_canvas_window(self, event: tk.Event) -> None:
        return

    def _show_list_overview(self) -> None:
        self.list_frame.tkraise()

    def _show_calendar_overview(self) -> None:
        self.calendar_overview_frame.tkraise()

    def _on_calendar_configure(self, _event: tk.Event | None = None) -> None:
        self.calendar_canvas.configure(scrollregion=self.calendar_canvas.bbox("all"))

    def _on_calendar_canvas_configure(self, event: tk.Event) -> None:
        required_width = self.calendar_frame.winfo_reqwidth()
        self.calendar_canvas.itemconfigure(self.calendar_canvas_window, width=max(event.width, required_width))

    def _bind_calendar_mousewheel(self, _event: tk.Event | None = None) -> None:
        if self._calendar_mousewheel_bound:
            return
        self.bind_all("<MouseWheel>", self._on_calendar_mousewheel)
        self._calendar_mousewheel_bound = True

    def _unbind_calendar_mousewheel(self, _event: tk.Event | None = None) -> None:
        if not self._calendar_mousewheel_bound:
            return
        self.unbind_all("<MouseWheel>")
        self._calendar_mousewheel_bound = False

    def _on_calendar_mousewheel(self, event: tk.Event) -> None:
        self.calendar_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def load_items(self) -> None:
        self.sync_google_calendars()
        self._refresh_calendar_filters()
        self.calendar_events = self._load_calendar_events()
        self.notes_with_dates = self._get_notes_with_date()
        self.actions = self.note_service.list_actions(limit=3000)
        self._events_by_day = self._group_entries_by_day(self.calendar_events, self.notes_with_dates)

    def log(self, message: str) -> None:
        logger.info(message)

    def render_current_calendar_view(self) -> None:
        """Reconstruye la vista actual del calendario (día/semana/mes) sin cambiar navegación."""
        self._clear_calendar_body()
        self.render_calendar()

    def refresh_calendar(self) -> None:
        """Recarga los datos y vuelve a renderizar la vista actual del calendario."""
        self.load_items()
        self.render_current_calendar_view()
        self._refresh_overview_list()
        if self._selected_record:
            self.show_detail(self._selected_record)
        self.log("Calendario actualizado automáticamente")

    def refresh_calendar_view(self) -> None:
        """Compatibilidad: usa el refresco central del calendario."""
        self.refresh_calendar()

    def refresh_overview(self) -> None:
        self.refresh_calendar()

    def _refresh_overview_list(self) -> None:
        self._overview_records.clear()
        self._all_rows = []

        for note in self.note_service.list_notes(limit=2000):
            record_type = "EMAIL" if note.source == "email_pasted" else "NOTE"
            email_link = ""
            if record_type == "EMAIL":
                source_id = (note.source_id or "").strip()
                if source_id.startswith("http://") or source_id.startswith("https://"):
                    email_link = source_id
                elif source_id:
                    email_link = f"https://mail.google.com/mail/u/0/#all/{source_id}"
            display_date = note.fecha or ""
            if note.hora_inicio:
                display_date = f"{display_date} {note.hora_inicio}".strip()
            if (note.tipo or "").strip().lower() == "evento":
                record_type = "EVENT"
            record = {
                "kind": record_type,
                "id": note.id,
                "note_id": note.id,
                "title": note.title or "(Nota sin título)",
                "status": note.estado or "Pendiente",
                "date": display_date,
                "time": note.hora_inicio or "",
                "raw_text": note.raw_text or "",
                "body": "",
                "description": "",
                "content": note.raw_text or "",
                "source_id": note.source_id or "",
                "email_link": email_link,
                "htmlLink": note.google_calendar_link or "",
                "google_calendar_link": note.google_calendar_link or "",
                "google_calendar_id": note.google_calendar_id or "",
                "google_event_id": note.google_event_id or "",
            }
            self._enrich_with_email_fields(record, note)
            self._append_overview_record(record)

        for action in self.actions:
            record = {
                "kind": "ACTION",
                "id": action.id,
                "title": action.description or "(Acción sin descripción)",
                "status": action.status or "pendiente",
                "date": action.completed_at or action.created_at,
                "description": action.description or "",
                "content": action.description or "",
                "note_id": action.note_id,
            }
            self._append_overview_record(record)

        for event in self.calendar_events:
            event_date = self._event_date(event)
            record = {
                "kind": "EVENT",
                "id": str(event.get("event_id") or event.get("id") or ""),
                "title": str(event.get("summary") or "(Sin título)"),
                "status": "Pendiente",
                "date": event_date.isoformat() if event_date else "",
                "description": str(event.get("description") or ""),
                "content": str(event.get("description") or ""),
                "htmlLink": str(event.get("htmlLink") or ""),
                "calendar_name": str(event.get("calendar_name") or ""),
                "background_color": str(event.get("background_color") or EVENT_COLOR),
                "foreground_color": _sanitize_tk_color(str(event.get("foreground_color") or "#000000")),
                "google_calendar_id": str(event.get("google_calendar_id") or "primary"),
            }
            self._append_overview_record(record)

        self.excel_filter.apply()

    def _append_overview_record(self, record: dict[str, str | int]) -> None:
        iid = f"{record['kind']}_{record['id']}"
        self._overview_records[iid] = record
        row = {
            "id": iid,
            "title": str(record.get("title") or ""),
            "tipo": self.TYPE_LABELS.get(str(record["kind"]), str(record["kind"])),
            "estado": str(record.get("status") or ""),
            "fecha": str(record.get("date") or ""),
            "kind": str(record.get("kind") or ""),
            "raw_text": str(record.get("raw_text") or ""),
            "body": str(record.get("body") or ""),
            "description": str(record.get("description") or ""),
            "content": str(record.get("content") or ""),
        }
        self._all_rows.append(row)

    def _rows_for_excel_filter(self) -> list[dict[str, str]]:
        text = self.search_var.get().lower().strip()
        enabled_kinds = {kind for kind, var in self._type_filter_vars.items() if var.get()}

        filtered: list[dict[str, str]] = []
        for row in self._all_rows:
            if str(row.get("kind") or "") not in enabled_kinds:
                continue
            if not text:
                filtered.append(row)
                continue

            searchable_text = " ".join(
                [
                    str(row.get("title", "")),
                    str(row.get("raw_text", "")),
                    str(row.get("body", "")),
                    str(row.get("description", "")),
                    str(row.get("content", "")),
                ]
            ).lower()
            if text in searchable_text:
                filtered.append(row)
        return filtered

    def _set_filtered_rows(self, rows: list[dict[str, str]]) -> None:
        selected_ids = set(self.tree.selection())
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for row in rows:
            record = self._overview_records.get(str(row["id"]))
            if record is None:
                continue
            tag = self._record_tag(record)
            if str(record.get("kind") or "") == "EVENT" and tag.endswith("PENDING"):
                bg = _sanitize_tk_color(str(record.get("background_color") or EVENT_COLOR), fallback=EVENT_COLOR)
                fg = _sanitize_tk_color(str(record.get("foreground_color") or "#000000"))
                self.tree.tag_configure(tag, background=bg, foreground=fg)
            values = (row["id"], row["title"], row["tipo"], row["estado"], row["fecha"])
            self.tree.insert("", "end", iid=row["id"], values=values, tags=(tag,))
            if row["id"] in selected_ids:
                self.tree.selection_add(row["id"])

        self._update_search_count(len(rows))

    def _clear_list_filters(self) -> None:
        self.search_var.set("")
        self.excel_filter.clear_all_filters()

    def _apply_global_search(self, _event: tk.Event | None = None) -> None:
        self.excel_filter.apply()

    def _update_search_count(self, count: int) -> None:
        self.search_count_var.set(f"Resultados encontrados: {count}")

    def _record_tag(self, record: dict[str, str | int]) -> str:
        status = str(record.get("status") or "").lower()
        kind = str(record.get("kind") or "NOTE")
        is_done = status in {"finalizado", "completado", "hecha", "done"}
        suffix = "DONE" if is_done else "PENDING"
        if kind == "EVENT" and not is_done:
            calendar_id = str(record.get("google_calendar_id") or "primary").replace("@", "_").replace(".", "_")
            return f"EVENT_{calendar_id}_{suffix}"
        return f"{kind}_{suffix}"

    def _on_overview_select(self, _event: tk.Event) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        record = self._overview_records.get(selected[0])
        if record:
            self.show_detail(record)

    def show_detail(self, record: dict[str, str | int]) -> None:
        self._restore_title_label()
        self._restore_status_label()
        self._restore_date_label()
        self._restore_time_label()
        self._selected_record = record
        kind = str(record.get("kind") or "NOTE")

        self.detail_title_var.set(str(record.get("title") or "(Sin título)"))
        self.detail_type_var.set(self.TYPE_LABELS.get(kind, kind))
        self.detail_status_var.set(str(record.get("status") or "-"))
        raw_date = str(record.get("date") or "").strip()
        parsed_date = raw_date.split(" ")[0] if raw_date else "-"
        parsed_time = str(record.get("time") or "").strip() or (raw_date.split(" ")[1] if " " in raw_date else "")
        self.detail_date_var.set(parsed_date)
        self.detail_time_var.set(parsed_time or "-")
        calendar_name = str(record.get("calendar_name") or "-") if kind == "EVENT" else "-"
        self.detail_calendar_var.set(calendar_name)
        if kind == "EVENT":
            self.detail_time_label.grid()
        else:
            self._restore_time_label()
            self.detail_time_var.set("-")

        self.content_text.configure(bg=_sanitize_tk_color(self._detail_bg(record), fallback="#ffffff"), fg=_sanitize_tk_color("#000000"))
        if kind == "EVENT":
            self.detail_calendar_label.configure(foreground=_sanitize_tk_color(str(record.get("foreground_color") or "#000000")))
        else:
            self.detail_calendar_label.configure(foreground=_sanitize_tk_color("#000000"))
        self.content_text.delete("1.0", "end")
        self.content_text.insert("1.0", str(record.get("content") or ""))

        self._render_associated_actions(record)
        self._render_email_attachments(record)
        self._render_context_actions(record)
        self._update_detail_scrollregion()

    def _detail_bg(self, record: dict[str, str | int]) -> str:
        status = str(record.get("status") or "").lower()
        kind = str(record.get("kind") or "NOTE")
        if status in {"finalizado", "completado", "hecha", "done"}:
            return NOTE_DONE_COLOR
        if kind == "EVENT":
            return str(record.get("background_color") or EVENT_COLOR)
        return self.TYPE_COLORS.get(kind, "#ffffff")


    def _on_overview_double_click(self, _event: tk.Event | None = None) -> None:
        item = self.selected_item
        if item and self._is_email_backed_record(item):
            # FIX: doble click en un email debe abrir gestor, seleccionar y mostrar el mensaje.
            self.abrir_email()
            return
        self.editar_registro()

    def _start_inline_title_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_title_entry is not None:
            self._inline_title_entry.focus_set()
            self._inline_title_entry.select_range(0, "end")
            return "break"
        inline_title_text = getattr(self, "_inline_title_text", None)
        if inline_title_text is not None:
            inline_title_text.focus_set()
            return "break"

        if not self._selected_record or self._note_id_for_record(self._selected_record) <= 0:
            return "break"

        current_title = self.detail_title_var.get().strip()
        # Oculta el Label y muestra un Entry inline en la misma posición.
        self.detail_title_label.pack_forget()
        if len(current_title) > 80:
            self._inline_title_text = tk.Text(self.detail_content, height=2, wrap="word")
            self._inline_title_text.pack(anchor="w", fill="x", before=self.detail_metadata)
            self._inline_title_text.insert("1.0", current_title)
            self._inline_title_text.focus_set()
            self._inline_title_text.bind("<KeyRelease>", self._auto_resize_title_text)
            self._inline_title_text.bind("<Control-Return>", self._save_inline_title_edit)
            self._inline_title_text.bind("<FocusOut>", self._save_inline_title_edit)
            self._auto_resize_title_text()
        else:
            self._inline_title_entry = ttk.Entry(self.detail_content)
            self._inline_title_entry.pack(anchor="w", fill="x", before=self.detail_metadata)
            self._inline_title_entry.insert(0, current_title)
            self._inline_title_entry.focus_set()
            self._inline_title_entry.select_range(0, "end")

            # Guardado inline con Enter o al perder foco.
            self._inline_title_entry.bind("<Return>", self._save_inline_title_edit)
            self._inline_title_entry.bind("<FocusOut>", self._save_inline_title_edit)
            self._inline_title_entry.bind("<KeyRelease>", self._auto_resize_entry)
            self._auto_resize_entry()
        return "break"

    def _auto_resize_entry(self, _event: tk.Event | None = None) -> None:
        if self._inline_title_entry is None:
            return
        text = self._inline_title_entry.get()
        self._inline_title_entry.configure(width=max(20, len(text) + 2))

    def _auto_resize_title_text(self, _event: tk.Event | None = None) -> None:
        if self._inline_title_text is None:
            return
        content = self._inline_title_text.get("1.0", "end").strip()
        wrapped_lines = max(1, len(content) // 80 + 1)
        self._inline_title_text.configure(height=max(2, min(6, wrapped_lines)))

    def _restore_title_label(self) -> None:
        if self._inline_title_entry is not None:
            self._inline_title_entry.destroy()
            self._inline_title_entry = None
        inline_title_text = getattr(self, "_inline_title_text", None)
        if inline_title_text is not None:
            inline_title_text.destroy()
            self._inline_title_text = None
        if not self.detail_title_label.winfo_manager():
            self.detail_title_label.pack(anchor="w", fill="x", before=self.detail_metadata)

    def _save_inline_title_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_title_saving:
            return "break"

        self._inline_title_saving = True
        if self._inline_title_entry is not None:
            new_title = self._inline_title_entry.get().strip() or "(Sin título)"
        elif getattr(self, "_inline_title_text", None) is not None:
            new_title = self._inline_title_text.get("1.0", "end").strip() or "(Sin título)"
        else:
            self._inline_title_saving = False
            return "break"

        note_id = self._note_id_for_record(self._selected_record or {})
        if note_id > 0:
            content = self.content_text.get("1.0", "end").strip()
            # Persistencia: actualiza título vía NoteService.
            self.note_service.update_note_title(note_id, new_title, content)

        if self._selected_record is not None:
            self._selected_record["title"] = new_title
        self.detail_title_var.set(new_title)

        # Restaura el Label y refresca la vista para propagar el nuevo título.
        self._restore_title_label()
        self._inline_title_saving = False
        self.refresh_calendar()
        return "break"


    def _generate_time_values(self) -> list[str]:
        values: list[str] = []
        for hour in range(24):
            for minute in (0, 30):
                values.append(f"{hour:02d}:{minute:02d}")
        return values

    def _is_event_record(self, record: dict[str, str | int] | None = None) -> bool:
        candidate = record or self._selected_record or {}
        return str(candidate.get("kind") or "").upper() == "EVENT"

    def _start_inline_status_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_status_widget is not None:
            self._inline_status_widget.focus_set()
            return "break"
        if not self._selected_record or self._note_id_for_record(self._selected_record) <= 0:
            return "break"
        self.detail_status_label.grid_remove()
        self._inline_status_widget = ttk.Combobox(self.detail_metadata, values=STATUS_OPTIONS, state="readonly", width=14)
        current_status = self.detail_status_var.get().strip()
        self._inline_status_widget.set(current_status if current_status in STATUS_OPTIONS else STATUS_OPTIONS[0])
        self._inline_status_widget.grid(row=0, column=3, sticky="w", padx=(0, 20))
        self._inline_status_widget.bind("<<ComboboxSelected>>", self._save_inline_status_edit)
        self._inline_status_widget.bind("<FocusOut>", self._save_inline_status_edit)
        self._inline_status_widget.focus_set()
        return "break"

    def _restore_status_label(self) -> None:
        if self._inline_status_widget is not None:
            self._inline_status_widget.destroy()
            self._inline_status_widget = None
        self.detail_status_label.grid()

    def _save_inline_status_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_status_saving or self._inline_status_widget is None:
            return "break"
        self._inline_status_saving = True
        new_status = self._inline_status_widget.get().strip() or STATUS_OPTIONS[0]
        note_id = self._note_id_for_record(self._selected_record or {})
        if note_id > 0:
            self.note_service.update_note_status(note_id, new_status)
        if self._selected_record is not None:
            self._selected_record["status"] = new_status
        self.detail_status_var.set(new_status)
        self._restore_status_label()
        self._inline_status_saving = False
        self.log("Estado modificado desde edición inline")
        self.refresh_calendar()
        return "break"

    def _start_inline_date_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_date_widget is not None:
            self._inline_date_widget.focus_set()
            return "break"
        if not self._selected_record or self._note_id_for_record(self._selected_record) <= 0:
            return "break"
        self.detail_date_label.grid_remove()
        current_date = str(self._selected_record.get("date") or "").split(" ")[0] or date.today().isoformat()
        selected_day = self._safe_parse_date(current_date) or date.today()
        self._inline_date_widget = DateEntry(self.detail_metadata, date_pattern="yyyy-mm-dd", state="readonly")
        self._inline_date_widget.set_date(selected_day)
        self._inline_date_widget.grid(row=0, column=5, sticky="w", padx=(0, 20))
        self._inline_date_widget.bind("<<DateEntrySelected>>", self._save_inline_date_edit)
        self._inline_date_widget.bind("<FocusOut>", self._save_inline_date_edit)
        self._inline_date_widget.focus_set()
        return "break"

    def _restore_date_label(self) -> None:
        if self._inline_date_widget is not None:
            self._inline_date_widget.destroy()
            self._inline_date_widget = None
        self.detail_date_label.grid()

    def _save_inline_date_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_date_saving or self._inline_date_widget is None:
            return "break"
        self._inline_date_saving = True
        selected_date = self._inline_date_widget.get_date().strftime("%Y-%m-%d")
        note_id = self._note_id_for_record(self._selected_record or {})
        if note_id > 0:
            self.note_service.update_note_date(note_id, selected_date)
        if self._selected_record is not None:
            self._selected_record["date"] = selected_date
        self.detail_date_var.set(selected_date)
        self._restore_date_label()
        self._inline_date_saving = False
        self.log("Fecha modificada desde edición inline")
        self.refresh_calendar()
        return "break"

    def _start_inline_time_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_time_widget is not None:
            self._inline_time_widget.focus_set()
            return "break"
        if not self._is_event_record() or not self._selected_record or self._note_id_for_record(self._selected_record) <= 0:
            return "break"
        self.detail_time_label.grid_remove()
        values = self._generate_time_values()
        self._inline_time_widget = ttk.Combobox(self.detail_metadata, values=values, state="readonly", width=8)
        current_time = str(self._selected_record.get("time") or "").strip()
        self._inline_time_widget.set(current_time if current_time in values else values[0])
        self._inline_time_widget.grid(row=0, column=7, sticky="w", padx=(0, 20))
        self._inline_time_widget.bind("<<ComboboxSelected>>", self._save_inline_time_edit)
        self._inline_time_widget.bind("<FocusOut>", self._save_inline_time_edit)
        self._inline_time_widget.focus_set()
        return "break"

    def _restore_time_label(self) -> None:
        if self._inline_time_widget is not None:
            self._inline_time_widget.destroy()
            self._inline_time_widget = None
        self.detail_time_label.grid()

    def _save_inline_time_edit(self, _event: tk.Event | None = None) -> str | None:
        if self._inline_time_saving or self._inline_time_widget is None:
            return "break"
        self._inline_time_saving = True
        selected_time = self._inline_time_widget.get().strip()
        note_id = self._note_id_for_record(self._selected_record or {})
        if note_id > 0 and selected_time:
            self.note_service.update_note_time(note_id, selected_time)
        if self._selected_record is not None:
            self._selected_record["time"] = selected_time
        self.detail_time_var.set(selected_time or "-")
        self._restore_time_label()
        self._inline_time_saving = False
        self.log("Fecha modificada desde edición inline")
        self.refresh_calendar()
        return "break"

    def _select_email_in_manager(self, item: dict[str, str | int], *, action_name: str) -> Any | None:
        """Open EmailManagerWindow and select the target email.

        Centralizes the integration flow Calendar -> EmailManager -> select gmail_id.
        """
        email_window = self._open_email_manager()
        if email_window is None:
            return None

        gmail_id = self._resolve_gmail_id(item)
        if not gmail_id:
            messagebox.showwarning("Email", "No se encontró el Gmail ID del correo.")
            return None

        if not email_window.select_email_by_gmail_id(gmail_id):
            # FIX: comportamiento seguro cuando el correo no está disponible.
            messagebox.showwarning("Email", f"No se encontró el correo para {action_name}.")
            return None

        email_window.focus_force()
        return email_window

    def _render_associated_actions(self, record: dict[str, str | int]) -> None:
        for child in self.associated_actions_frame.winfo_children():
            child.destroy()
        self._action_vars.clear()
        self._inline_action_row = None
        self._inline_action_entry = None
        self._inline_action_saving = False

        note_id = self._note_id_for_record(record)
        if note_id <= 0:
            ttk.Label(self.associated_actions_frame, text="No aplica para este registro.").pack(anchor="w", padx=6, pady=4)
            return

        note_actions = self.note_service.list_actions_by_note(note_id)
        if not note_actions:
            ttk.Label(self.associated_actions_frame, text="Sin acciones asociadas.").pack(anchor="w", padx=6, pady=4)
            return

        for action in note_actions:
            var = tk.BooleanVar(value=action.status == "hecha")
            self._action_vars[action.id] = var
            cb = ttk.Checkbutton(
                self.associated_actions_frame,
                text=action.description,
                variable=var,
                command=lambda aid=action.id: self._toggle_action_from_detail(aid),
            )
            cb.pack(anchor="w", padx=6, pady=2)

    def _toggle_action_from_detail(self, action_id: int) -> None:
        self.note_service.toggle_action_status(action_id)
        if self._selected_record and str(self._selected_record.get("kind")) in {"NOTE", "EMAIL"}:
            note_id = int(self._selected_record.get("id") or 0)
            note = self.note_service.get_note_by_id(note_id)
            if note:
                self._selected_record["status"] = note.estado
        self.refresh_calendar()

    def _render_context_actions(self, record: dict[str, str | int]) -> None:
        for frame in (self.detail_context_buttons, self.actions_context_buttons, self.attachments_context_buttons):
            for child in frame.winfo_children():
                child.destroy()

        kind = str(record.get("kind") or "NOTE")
        is_email = self._is_email_backed_record(record)
        email_buttons_state = "normal" if is_email else "disabled"
        has_note = self._note_id_for_record(record) > 0

        if has_note and kind in {"NOTE", "EMAIL", "EVENT"}:
            ttk.Button(self.detail_context_buttons, text="Editar", command=self.editar_registro).pack(side="left", padx=4)
            ttk.Button(self.detail_context_buttons, text="Completar", command=self.completar_registro).pack(side="left", padx=4)
            ttk.Button(self.actions_context_buttons, text="Nueva acción", command=self.crear_accion).pack(side="left", padx=4)
        elif kind == "ACTION":
            ttk.Button(self.actions_context_buttons, text="Marcar completada", command=self.completar_registro).pack(side="left", padx=4)
            ttk.Button(self.actions_context_buttons, text="Editar", command=self.editar_registro).pack(side="left", padx=4)

        if kind == "EVENT":
            ttk.Button(self.detail_context_buttons, text="Abrir en Google Calendar", command=self.abrir_evento_calendar).pack(side="left", padx=4)

        ttk.Button(self.attachments_context_buttons, text="Responder", command=self.responder_email, state=email_buttons_state).pack(side="left", padx=4)
        ttk.Button(self.attachments_context_buttons, text="Reenviar", command=self.reenviar_email, state=email_buttons_state).pack(side="left", padx=4)
        ttk.Button(self.attachments_context_buttons, text="Abrir email", command=self.abrir_email, state=email_buttons_state).pack(side="left", padx=4)

    def _resolve_gmail_id(self, item: dict[str, str | int]) -> str:
        gmail_id = str(item.get("gmail_id") or "").strip()
        if gmail_id:
            return gmail_id
        source_id = str(item.get("source_id") or "").strip()
        if source_id:
            return source_id
        email_link = str(item.get("email_link") or "")
        if "#" in email_link:
            return email_link.rsplit("/", 1)[-1].strip()
        return ""

    def _open_email_manager(self):
        if self.open_email_manager_callback is None:
            return None
        return self.open_email_manager_callback()

    def _render_email_attachments(self, record: dict[str, str | int]) -> None:
        self._attachments_by_name.clear()
        self.attachments_list.delete(0, "end")

        if not self._is_email_backed_record(record):
            self.attachments_list.insert("end", "(solo disponible para emails)")
            return

        email_window = self._open_email_manager()
        gmail_id = self._resolve_gmail_id(record)
        if email_window is None or not gmail_id:
            for idx, attachment in enumerate(record.get("adjuntos") or [], start=1):
                if not isinstance(attachment, dict):
                    continue
                name = str(attachment.get("filename") or f"adjunto_{idx}")
                display_name = f"📄 {name}"
                self._attachments_by_name[display_name] = attachment
                self.attachments_list.insert("end", display_name)
            if not self._attachments_by_name:
                self.attachments_list.insert("end", "(sin adjuntos)")
            return

        attachments = email_window.get_email_attachments(gmail_id)
        if not attachments:
            # FIX: fallback a record["adjuntos"] cuando el caché del EmailManager no trae datos.
            attachments = [att for att in (record.get("adjuntos") or []) if isinstance(att, dict)]
        if not attachments:
            self.attachments_list.insert("end", "(sin adjuntos)")
            return

        for idx, attachment in enumerate(attachments, start=1):
            name = str(attachment.get("filename") or f"adjunto_{idx}")
            display_name = f"📄 {name}"
            self._attachments_by_name[display_name] = attachment
            self.attachments_list.insert("end", display_name)

    def _on_attachment_open(self, _event: tk.Event | None = None) -> None:
        item = self.selected_item
        if not item or not self._is_email_backed_record(item):
            return
        selection = self.attachments_list.curselection()
        if not selection:
            return
        display_name = self.attachments_list.get(selection[0])
        attachment = self._attachments_by_name.get(display_name)
        if not attachment:
            return
        email_window = self._open_email_manager()
        gmail_id = self._resolve_gmail_id(item)
        if email_window is None or not gmail_id:
            return
        email_window.open_attachment(gmail_id, attachment)

    @property
    def selected_item(self) -> dict[str, str | int] | None:
        return self._selected_record

    def actualizar_ui(self) -> None:
        self.refresh_calendar()

    def recargar_agenda(self) -> None:
        self.refresh_calendar()

    def editar_registro(self) -> None:
        item = self.selected_item
        if not item:
            return
        kind = str(item.get("kind") or "")
        if self._note_id_for_record(item) > 0 and kind in {"NOTE", "EMAIL", "EVENT"}:
            self._save_note_edits()
        elif kind == "ACTION":
            self._save_action_edits()
        elif kind == "EVENT":
            self._save_event_edits()

    def completar_registro(self) -> None:
        item = self.selected_item
        if not item:
            return

        item["status"] = "Completado"

        kind = str(item.get("kind") or "")
        if self._note_id_for_record(item) > 0 and kind in {"NOTE", "EMAIL", "EVENT"}:
            self._complete_current_note()
            if self._is_email_backed_record(item):
                self._prompt_email_response(item)
        elif kind == "ACTION":
            self._complete_current_action()
        else:
            self.actualizar_ui()

    def _prompt_email_response(self, item: dict[str, str | int]) -> None:
        gmail_id = self._resolve_gmail_id(item)
        if not gmail_id:
            messagebox.showwarning("Email", "No se encontró el Gmail ID para responder automáticamente.")
            return
        completion_event: dict[str, str | int] = {
            "gmail_id": gmail_id,
            "thread_id": str(item.get("thread_id") or ""),
            "to": str(item.get("remitente") or ""),
            "subject": f"Re: {str(item.get('asunto') or item.get('title') or '').strip()}",
            "body": str(item.get("content") or ""),
        }
        if self.email_completion_callback is not None:
            self.email_completion_callback(completion_event)
            return
        if not messagebox.askyesno("Email", "Responder al email ahora."):
            return
        self.responder_email()

    def crear_accion(self) -> None:
        item = self.selected_item
        if not item:
            return

        note_id = self._note_id_for_record(item)
        if note_id <= 0:
            return

        # Popup dedicado para reducir ruido visual en la zona de detalle.
        description = simpledialog.askstring("Nueva acción", "Descripción de la acción:", parent=self)
        if description is None:
            return
        cleaned = description.strip()
        if not cleaned:
            return

        note = self.note_service.get_note_by_id(note_id)
        if not note:
            return

        self.note_service.actions_repo.create_action(note_id, cleaned, note.area)
        self.refresh_calendar()

    def _save_inline_action(self, _event: tk.Event | None = None) -> str | None:
        if getattr(self, "_inline_action_saving", False) or getattr(self, "_inline_action_entry", None) is None:
            return "break"

        self._inline_action_saving = True
        description = self._inline_action_entry.get().strip()

        if self._inline_action_row is not None:
            self._inline_action_row.destroy()
        self._inline_action_row = None
        self._inline_action_entry = None
        self._inline_action_saving = False

        if not description:
            return "break"

        if not self._selected_record:
            return "break"

        note_id = self._note_id_for_record(self._selected_record)
        if note_id <= 0:
            return "break"

        note = self.note_service.get_note_by_id(note_id)
        if not note:
            return "break"

        self.note_service.actions_repo.create_action(note_id, description, note.area)
        self.refresh_calendar()
        self._render_associated_actions(self._selected_record)
        self._update_detail_scrollregion()
        return "break"

    def abrir_email(self) -> None:
        item = self.selected_item
        if not item or not self._is_email_backed_record(item):
            return

        # FIX: Abrir email desde calendario usa el mismo flujo de selección del gestor de emails.
        self._select_email_in_manager(item, action_name="abrir")

    def _edit_actions_for_note(self, note_id: int) -> None:
        actions = self.note_service.list_actions_by_note(note_id)
        for action in actions:
            new_value = simpledialog.askstring(
                "Editar acción",
                "Descripción de la acción (vacío para eliminar):",
                initialvalue=action.description,
                parent=self,
            )
            if new_value is None:
                continue
            cleaned = new_value.strip()
            if cleaned:
                self.note_service.update_action_description(action.id, cleaned)
            else:
                self.note_service.delete_action(action.id)

    def responder_email(self) -> None:
        item = self.selected_item
        if not item or not self._is_email_backed_record(item):
            return
        email_window = self._select_email_in_manager(item, action_name="responder")
        if email_window is None:
            return

        # FIX: reutiliza la misma acción del botón "Responder" del EmailManagerWindow.
        create_reply = getattr(email_window, "_create_outlook_draft", None)
        if callable(create_reply):
            create_reply()
        logger.info("Responder email: %s", item.get("id"))

    def reenviar_email(self) -> None:
        item = self.selected_item
        if not item or not self._is_email_backed_record(item):
            return
        email_window = self._select_email_in_manager(item, action_name="reenviar")
        if email_window is None:
            return

        # FIX: reutiliza la misma acción del botón "Reenviar" del EmailManagerWindow.
        forward = getattr(email_window, "_forward_email", None)
        if callable(forward):
            forward()
        logger.info("Reenviar email: %s", item.get("id"))

    def abrir_evento_calendar(self) -> None:
        item = self.selected_item
        if not item:
            return

        link = str(item.get("google_calendar_link") or item.get("htmlLink") or "")

        if link:
            try:
                webbrowser.open(link)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo abrir el evento en Google Calendar")
                messagebox.showwarning("Evento", "No se pudo abrir el evento en Google Calendar")
        else:
            logger.warning("No se pudo abrir evento de Google Calendar")
            messagebox.showwarning(
                "Evento",
                "Este evento no tiene enlace a Google Calendar",
            )

    def _save_event_edits(self) -> None:
        if not self._selected_record or self.calendar_client is None:
            return

        event_id = str(self._selected_record.get("google_event_id") or self._selected_record.get("id") or "").strip()
        if not event_id:
            return

        calendar_id = str(self._selected_record.get("google_calendar_id") or "").strip() or "primary"
        data = {
            "summary": self.detail_title_var.get().strip() or "(Sin título)",
            "description": self.content_text.get("1.0", "end").strip(),
        }
        try:
            self.calendar_client.update_event(calendar_id=calendar_id, event_id=event_id, data=data)
            logger.info("Evento actualizado en calendario %s", calendar_id)
            self.refresh_calendar()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo actualizar evento en Google Calendar")
            messagebox.showwarning("Evento", "No se pudo actualizar el evento en Google Calendar")

    def _save_note_edits(self) -> None:
        if not self._selected_record:
            return
        note_id = self._note_id_for_record(self._selected_record)
        if note_id <= 0:
            return

        title = simpledialog.askstring("Editar", "Título:", initialvalue=self.detail_title_var.get().strip(), parent=self)
        if title is None:
            return

        picker = tk.Toplevel(self)
        picker.title("Seleccionar fecha y hora")
        picker.transient(self.winfo_toplevel())
        picker.grab_set()

        selected_date: dict[str, str] = {"value": ""}
        selected_time: dict[str, str] = {"value": ""}
        current_date = str(self._selected_record.get("date") or "").split(" ")[0] or date.today().isoformat()
        current_day = self._safe_parse_date(current_date) or date.today()

        ttk.Label(picker, text="Fecha:").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        date_picker = DateEntry(picker, date_pattern="yyyy-mm-dd", state="readonly")
        date_picker.set_date(current_day)
        date_picker.grid(row=0, column=1, sticky="w", padx=8, pady=8)

        time_combo = None
        if self._is_event_record(self._selected_record):
            ttk.Label(picker, text="Hora:").grid(row=1, column=0, sticky="w", padx=8, pady=8)
            time_combo = ttk.Combobox(picker, values=self._generate_time_values(), state="readonly", width=8)
            current_time = str(self._selected_record.get("time") or "").strip()
            time_combo.set(current_time if current_time else "09:00")
            time_combo.grid(row=1, column=1, sticky="w", padx=8, pady=8)

        def _accept() -> None:
            selected_date["value"] = date_picker.get_date().strftime("%Y-%m-%d")
            selected_time["value"] = time_combo.get().strip() if time_combo is not None else ""
            picker.destroy()

        ttk.Button(picker, text="Guardar", command=_accept).grid(row=2, column=0, columnspan=2, pady=(4, 10))
        self.wait_window(picker)

        date_value = selected_date["value"].strip()
        if not date_value:
            return

        content = self.content_text.get("1.0", "end").strip()
        self.note_service.update_note_title(note_id, title, content)
        self.note_service.update_note_date(note_id, date_value)
        if self._is_event_record(self._selected_record) and selected_time["value"]:
            self.note_service.update_note_time(note_id, selected_time["value"])

        self.detail_title_var.set(title.strip() or "(Sin título)")
        self._selected_record["title"] = title.strip() or "(Sin título)"
        self._selected_record["date"] = date_value
        if self._is_event_record(self._selected_record) and selected_time["value"]:
            self._selected_record["time"] = selected_time["value"]
            self.detail_time_var.set(selected_time["value"])

        self._edit_actions_for_note(note_id)
        self.refresh_calendar()

    def _save_action_edits(self) -> None:
        if not self._selected_record:
            return
        action_id = int(self._selected_record.get("id") or 0)
        content = self.content_text.get("1.0", "end").strip()
        self.note_service.update_action_description(action_id, content)
        self.refresh_calendar()

    def _create_action_for_current_note(self) -> None:
        if not self._selected_record:
            return
        note_id = self._note_id_for_record(self._selected_record)
        note = self.note_service.get_note_by_id(note_id)
        if not note:
            return
        description = self.content_text.get("1.0", "end").strip().splitlines()[0][:180] or "Nueva acción"
        self.note_service.actions_repo.create_action(note_id, description, note.area)
        self.refresh_calendar()

    def _complete_current_note(self) -> None:
        if not self._selected_record:
            return
        note_id = self._note_id_for_record(self._selected_record)
        if note_id <= 0:
            return
        action_ids = [action.id for action in self.note_service.list_actions_by_note(note_id) if action.status != "hecha"]
        self.note_service.mark_actions_done(action_ids)
        self.note_service.note_repo.update_estado(note_id, "Finalizado")
        self.refresh_calendar()

    def _complete_current_action(self) -> None:
        if not self._selected_record:
            return
        action_id = int(self._selected_record.get("id") or 0)
        self.note_service.toggle_action_status(action_id)
        self.refresh_calendar()

    def _edit_event(self) -> None:
        self.abrir_evento_calendar()

    def _open_event(self) -> None:
        self.abrir_evento_calendar()

    def sync_google_calendars(self) -> None:
        if self.calendar_repo is None or self.calendar_client is None:
            return

        try:
            calendars = self.calendar_client.list_calendars()
            now_iso = datetime.utcnow().isoformat(timespec="seconds")
            valid_ids: list[str] = []
            for calendar in calendars:
                calendar_id = str(calendar.get("google_calendar_id") or "").strip()
                if not calendar_id:
                    continue
                valid_ids.append(calendar_id)
                self.calendar_repo.upsert_calendar(
                    google_calendar_id=calendar_id,
                    name=str(calendar.get("name") or calendar_id),
                    background_color=str(calendar.get("background_color") or "#9E9E9E"),
                    foreground_color=_sanitize_tk_color(str(calendar.get("foreground_color") or "#000000")),
                    is_primary=int(calendar.get("is_primary") or 0),
                    access_role=str(calendar.get("access_role") or ""),
                    selected=int(calendar.get("selected") if calendar.get("selected") is not None else 1),
                    updated_at=now_iso,
                )
            self.calendar_repo.delete_missing_calendars(valid_ids)
            logger.info("Calendarios sincronizados: %s", len(valid_ids))
        except Exception:  # noqa: BLE001
            logger.exception("Error sincronizando calendarios")
            messagebox.showwarning("Google Calendar", "No se pudieron sincronizar los calendarios")

    def _initialize_client(self) -> None:
        credentials_path = Path(r"C:\notion-second-brain\secrets\calendar_credentials.json")
        token_path = Path(r"C:\notion-second-brain\secrets\calendar_token.json")

        if not credentials_path.exists():
            credentials_path = Path("secrets/calendar_credentials.json")
        if not token_path.parent.exists():
            token_path = Path("secrets/calendar_token.json")

        try:
            self.calendar_client = GoogleCalendarClient(
                str(credentials_path),
                str(token_path),
                auth_event_callback=self._on_google_auth_event,
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo inicializar Google Calendar")
            self.calendar_client = None

    def _go_previous_month(self) -> None:
        if self.view_mode == "day":
            self.current_date -= timedelta(days=1)
        elif self.view_mode == "week":
            self.current_date -= timedelta(days=7)
        else:
            year = self.current_month.year
            month = self.current_month.month - 1
            if month < 1:
                month = 12
                year -= 1
            self.current_month = date(year, month, 1)
            self.current_date = self.current_month

        self.current_month = self.current_date.replace(day=1)
        self.render_calendar()

    def _go_next_month(self) -> None:
        if self.view_mode == "day":
            self.current_date += timedelta(days=1)
        elif self.view_mode == "week":
            self.current_date += timedelta(days=7)
        else:
            year = self.current_month.year
            month = self.current_month.month + 1
            if month > 12:
                month = 1
                year += 1
            self.current_month = date(year, month, 1)
            self.current_date = self.current_month

        self.current_month = self.current_date.replace(day=1)
        self.render_calendar()

    def _go_today(self) -> None:
        self.current_date = date.today()
        self.current_month = self.current_date.replace(day=1)
        self.render_calendar()

    def _set_view_day(self) -> None:
        self.view_mode = "day"
        self.render_calendar()

    def _set_view_week(self) -> None:
        self.view_mode = "week"
        self.render_calendar()

    def _set_view_month(self) -> None:
        self.view_mode = "month"
        self.render_calendar()

    def _clear_calendar_body(self) -> None:
        for child in self.calendar_frame.winfo_children():
            child.destroy()

    def render_calendar(self) -> None:
        self._update_week_label(self.current_date)
        if self.view_mode == "week":
            self._render_week_view()
            return
        if self.view_mode == "day":
            self._render_day_view()
            return
        self._render_month_view()

    def _render_month_view(self) -> None:
        self._clear_calendar_body()
        self._build_month_grid()
        self.month_label.configure(text=self.current_month.strftime("%B %Y").capitalize())
        self._label_metadata.clear()

        month_matrix = self._month_matrix(self.current_month.year, self.current_month.month)

        for index, day_value in enumerate(month_matrix):
            cell = self.cells[index]
            day_number_label = self.day_number_labels[index]
            events_frame = self.day_events_frames[index]

            for child in events_frame.winfo_children():
                child.destroy()

            if day_value.month != self.current_month.month:
                cell.configure(bg=_sanitize_tk_color("#f5f5f5"))
                day_number_label.configure(text=str(day_value.day), foreground=_sanitize_tk_color("#9ca3af"))
                continue

            cell.configure(bg=_sanitize_tk_color("#ffffff", fallback="#ffffff"))
            today = date.today()
            if day_value == today:
                day_number_label.configure(text=str(day_value.day), foreground=_sanitize_tk_color("#1d4ed8"))
            else:
                day_number_label.configure(text=str(day_value.day), foreground=_sanitize_tk_color("#111827"))

            day_entries = self._events_by_day.get(day_value, [])
            for entry in day_entries[:3]:
                text = self._entry_label_text(entry)
                label = tk.Label(events_frame, text=text, anchor="w", justify="left", cursor="hand2", wraplength=170, bg=_sanitize_tk_color(self._detail_bg(entry), fallback="#ffffff"), fg=_sanitize_tk_color("#000000"))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                self._label_metadata[str(label)] = entry

    def _build_month_grid(self) -> None:
        container = ttk.Frame(self.calendar_frame)
        container.pack(fill="both", expand=True)

        weekday_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for index, name in enumerate(weekday_names):
            label = ttk.Label(container, text=name, style="Weekday.TLabel")
            label.grid(row=0, column=index, sticky="nsew", padx=2, pady=(0, 4))
            container.columnconfigure(index, weight=1, uniform="calendar-col")

        self.cells: list[tk.Frame] = []
        self.day_number_labels: list[ttk.Label] = []
        self.day_events_frames: list[ttk.Frame] = []

        for week in range(6):
            container.rowconfigure(week + 1, weight=1, uniform="calendar-row", minsize=120)
            for day in range(7):
                cell = tk.Frame(container, bg=_sanitize_tk_color("#ffffff", fallback="#ffffff"), highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=6, pady=6)
                cell.grid(row=week + 1, column=day, sticky="nsew", padx=2, pady=2)
                cell.grid_propagate(False)

                day_label = ttk.Label(cell, text="", style="DayNumber.TLabel")
                day_label.pack(anchor="nw")

                events_frame = ttk.Frame(cell)
                events_frame.pack(fill="both", expand=True, pady=(4, 0))

                self.cells.append(cell)
                self.day_number_labels.append(day_label)
                self.day_events_frames.append(events_frame)

    def _render_week_view(self) -> None:
        self._clear_calendar_body()
        self._label_metadata.clear()
        self._week_day_columns = {}
        self._reset_drag_state()

        container = ttk.Frame(self.calendar_frame)
        container.pack(fill="both", expand=True)

        week_start = self.current_date - timedelta(days=self.current_date.weekday())
        week_end = week_start + timedelta(days=6)
        self.month_label.configure(text=f"Semana {week_start.day}–{week_end.day} {week_end.strftime('%B %Y').capitalize()}")

        weekday_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for index, day_name in enumerate(weekday_names):
            container.columnconfigure(index, weight=1, uniform="week-col")
            day_value = week_start + timedelta(days=index)

            column = tk.Frame(container, bg=_sanitize_tk_color("#ffffff", fallback="#ffffff"), highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=6, pady=6)
            column.grid(row=0, column=index, sticky="nsew", padx=2, pady=2)
            self._week_day_columns[str(column)] = day_value

            ttk.Label(column, text=f"{day_name} {day_value.day}", style="Weekday.TLabel").pack(anchor="w")

            day_entries = self._events_by_day.get(day_value, [])
            for entry in day_entries:
                text = self._entry_label_text(entry)
                label = tk.Label(column, text=text, anchor="w", justify="left", cursor="hand2", wraplength=165, bg=_sanitize_tk_color(self._detail_bg(entry), fallback="#ffffff"), fg=_sanitize_tk_color("#000000"))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                label.bind("<ButtonPress-1>", self._on_drag_start, add="+")
                label.bind("<B1-Motion>", self._on_drag_motion, add="+")
                label.bind("<ButtonRelease-1>", self._on_drag_release, add="+")
                self._label_metadata[str(label)] = entry

    def _on_drag_start(self, event: tk.Event) -> None:
        widget = event.widget
        entry_data = self._label_metadata.get(str(widget))
        if not entry_data:
            return

        self.dragged_entry = entry_data
        self._dragged_widget = widget
        self.drag_origin_day = self._day_from_widget(widget)
        self._drag_target_day = self.drag_origin_day
        widget.configure(cursor="fleur", highlightthickness=2, highlightbackground="#1d4ed8", relief="solid")

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self.dragged_entry is None or self._dragged_widget is None:
            return
        pointer_widget = self.winfo_containing(event.x_root, event.y_root)
        target_day = self._day_from_widget(pointer_widget)
        self._drag_target_day = target_day
        self._highlight_drag_target(pointer_widget)

    def _on_drag_release(self, event: tk.Event) -> None:
        if self.dragged_entry is None or self.drag_origin_day is None:
            self._reset_drag_state()
            return

        pointer_widget = self.winfo_containing(event.x_root, event.y_root)
        target_day = self._day_from_widget(pointer_widget)
        if target_day is None or target_day == self.drag_origin_day:
            self._reset_drag_state()
            return

        moved = self._update_dragged_entry_date(self.dragged_entry, target_day)
        self._reset_drag_state()
        if moved:
            self.log(f"Elemento movido a {target_day.isoformat()}")
            self.refresh_calendar()

    def _day_from_widget(self, widget: tk.Widget | None) -> date | None:
        current = widget
        while current is not None:
            day_value = self._week_day_columns.get(str(current))
            if day_value is not None:
                return day_value
            current = current.master
        return None

    def _highlight_drag_target(self, widget: tk.Widget | None) -> None:
        target_widget = self._column_widget_from_child(widget)
        if target_widget is self._drag_hover_column:
            return
        if self._drag_hover_column is not None:
            self._drag_hover_column.configure(highlightbackground="#d1d5db")
        if target_widget is not None:
            target_widget.configure(highlightbackground="#1d4ed8")
        self._drag_hover_column = target_widget

    def _column_widget_from_child(self, widget: tk.Widget | None) -> tk.Widget | None:
        current = widget
        while current is not None:
            if str(current) in self._week_day_columns:
                return current
            current = current.master
        return None

    def _update_dragged_entry_date(self, entry: dict[str, str | int], target_day: date) -> bool:
        target_date = target_day.isoformat()
        kind = str(entry.get("kind") or "")

        if kind in {"NOTE", "EMAIL"}:
            note_id = self._note_id_for_record(entry)
            if note_id <= 0:
                return False
            self.note_service.update_note_date(note_id, target_date)
            return True

        if kind == "ACTION":
            action_id = int(entry.get("id") or 0)
            if action_id <= 0:
                return False
            self.note_service.update_action_date(action_id, target_date)
            return True

        if kind == "EVENT":
            calendar_id = str(entry.get("google_calendar_id") or "").strip() or "primary"
            event_id = str(entry.get("google_event_id") or entry.get("id") or "").strip()
            if event_id and self.calendar_client is not None and not entry.get("note_id"):
                self._move_google_calendar_event(calendar_id, event_id, entry, target_day)
                return True

            note_id = self._note_id_for_record(entry)
            if note_id <= 0:
                return False
            self.note_service.update_note_date(note_id, target_date)
            return True

        return False

    def _move_google_calendar_event(self, calendar_id: str, event_id: str, entry: dict[str, str | int], target_day: date) -> None:
        if self.calendar_client is None:
            return
        start_time = str(entry.get("time") or "")
        start_dt = datetime.combine(target_day, datetime.min.time())
        if start_time:
            minutes = self._to_minutes(start_time)
            if minutes is not None:
                start_dt = start_dt + timedelta(minutes=minutes)

        payload: dict[str, dict[str, str]]
        if start_time:
            payload = {
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Madrid"},
                "end": {"dateTime": (start_dt + timedelta(hours=1)).isoformat(), "timeZone": "Europe/Madrid"},
            }
        else:
            next_day = target_day + timedelta(days=1)
            payload = {
                "start": {"date": target_day.isoformat()},
                "end": {"date": next_day.isoformat()},
            }
        self.calendar_client.update_event(calendar_id=calendar_id, event_id=event_id, data=payload)

    def _reset_drag_state(self) -> None:
        if self._dragged_widget is not None:
            self._dragged_widget.configure(cursor="hand2", highlightthickness=0, relief="flat")
        if self._drag_hover_column is not None:
            self._drag_hover_column.configure(highlightbackground="#d1d5db")
        self.dragged_entry = None
        self.drag_origin_day = None
        self._dragged_widget = None
        self._drag_target_day = None
        self._drag_hover_column = None

    def _render_day_view(self) -> None:
        self._clear_calendar_body()
        self._label_metadata.clear()
        self.month_label.configure(text=self.current_date.strftime("%A %d %B %Y").capitalize())

        container = ttk.Frame(self.calendar_frame)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        entries = list(self._events_by_day.get(self.current_date, []))
        entries.extend(self._actions_for_day(self.current_date))
        slots = self._time_slots(entries)
        timed_slot_map, top_section_entries = self._partition_day_entries(entries, slots)

        top_label = ttk.Label(container, text="Eventos del día (sin hora fija)", style="CalendarHeader.TLabel")
        top_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        top_frame = tk.Frame(container, bg=_sanitize_tk_color("#ffffff", fallback="#ffffff"), highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=8, pady=6)
        top_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 10))
        container.rowconfigure(1, weight=0)

        if not top_section_entries:
            ttk.Label(top_frame, text="Sin elementos sin hora fija.").pack(anchor="w")
        else:
            for entry in top_section_entries:
                text = self._entry_label_text(entry, include_time=True)
                label = tk.Label(top_frame, text=text, anchor="w", justify="left", cursor="hand2", wraplength=600, bg=_sanitize_tk_color(self._detail_bg(entry), fallback="#ffffff"), fg=_sanitize_tk_color("#000000"))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                self._label_metadata[str(label)] = entry

        timeline_label = ttk.Label(container, text="Timeline por horas", style="CalendarHeader.TLabel")
        timeline_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))

        for row, slot in enumerate(slots, start=3):
            time_label = ttk.Label(container, text=slot, style="Time.TLabel")
            time_label.grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=1)

            slot_frame = tk.Frame(container, bg=_sanitize_tk_color("#ffffff", fallback="#ffffff"), highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=6, pady=4)
            slot_frame.grid(row=row, column=1, sticky="nsew", pady=1)

            for entry in timed_slot_map.get(slot, []):
                include_entry_time = str(entry.get("time") or "") != slot
                text = self._entry_label_text(entry, include_time=include_entry_time)
                label = tk.Label(slot_frame, text=text, anchor="w", justify="left", cursor="hand2", wraplength=500, bg=_sanitize_tk_color(self._detail_bg(entry), fallback="#ffffff"), fg=_sanitize_tk_color("#000000"))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                self._label_metadata[str(label)] = entry

        self.log("Vista diaria reconstruida con eventos sin hora y timeline")

    def _partition_day_entries(
        self, entries: list[dict[str, str | int]], slots: list[str]
    ) -> tuple[dict[str, list[dict[str, str | int]]], list[dict[str, str | int]]]:
        timed_slot_map: dict[str, list[dict[str, str | int]]] = {}
        top_section: list[tuple[int | float, int, dict[str, str | int]]] = []
        slot_lookup = set(slots)

        for index, entry in enumerate(entries):
            entry_time = str(entry.get("time") or "")
            if not entry_time:
                top_section.append((float("inf"), index, entry))
                continue

            if entry_time in slot_lookup:
                timed_slot_map.setdefault(entry_time, []).append(entry)
                continue

            nearest_slot = self._nearest_slot(entry_time, slots)
            if nearest_slot:
                timed_slot_map.setdefault(nearest_slot, []).append(entry)
                continue

            minutes = self._to_minutes(entry_time)
            top_section.append((minutes if minutes is not None else float("inf"), index, entry))

        top_section.sort(key=lambda item: self._top_entry_sort_key(item[2], item[1]))
        return timed_slot_map, [entry for _, _, entry in top_section]


    def _top_entry_sort_key(self, entry: dict[str, str | int], index: int) -> tuple[int | float, str, int]:
        entry_time = str(entry.get("time") or "")
        entry_date = str(entry.get("date") or "")
        if entry_time:
            minutes = self._to_minutes(entry_time)
            return (minutes if minutes is not None else float("inf"), "", index)

        created_value = str(entry.get("created_at") or "")
        if created_value:
            return (float("inf"), created_value, index)

        if entry_date:
            return (float("inf"), entry_date, index)

        return (float("inf"), "", index)

    def _actions_for_day(self, day_value: date) -> list[dict[str, str | int]]:
        day_actions: list[dict[str, str | int]] = []
        for action in self.actions:
            action_date = self._safe_parse_date(action.completed_at or action.created_at)
            if action_date != day_value:
                continue
            day_actions.append(
                {
                    "origin": "ACTION",
                    "kind": "ACTION",
                    "title": action.description or "(Acción sin descripción)",
                    "id": action.id,
                    "note_id": action.note_id,
                    "status": action.status or "pendiente",
                    "date": (action.completed_at or action.created_at or "").split("T", maxsplit=1)[0],
                    "time": "",
                    "content": action.description or "",
                    "created_at": action.created_at,
                }
            )
        return day_actions

    def _nearest_slot(self, entry_time: str, slots: list[str]) -> str | None:
        entry_minutes = self._to_minutes(entry_time)
        if entry_minutes is None:
            return None
        valid_slots = [(slot, self._to_minutes(slot)) for slot in slots]
        valid_slots = [(slot, minutes) for slot, minutes in valid_slots if minutes is not None]
        if not valid_slots:
            return None
        return min(valid_slots, key=lambda item: abs(item[1] - entry_minutes))[0]

    def _on_entry_click(self, event: tk.Event) -> None:
        widget = event.widget
        entry_data = self._label_metadata.get(str(widget), {})
        if entry_data:
            self.show_detail(entry_data)

    def _time_slots(self, entries: list[dict[str, str | int]]) -> list[str]:
        timed_entries = [str(entry.get("time")) for entry in entries if entry.get("time")]
        if not timed_entries:
            start_minutes = 8 * 60
            end_minutes = 18 * 60
        else:
            minutes_list = [self._to_minutes(time_value) for time_value in timed_entries]
            valid_minutes = [value for value in minutes_list if value is not None]
            if not valid_minutes:
                start_minutes = 8 * 60
                end_minutes = 18 * 60
            else:
                start_minutes = max(0, min(valid_minutes) - 60)
                end_minutes = min(23 * 60 + 30, max(valid_minutes) + 60)

        start_minutes -= start_minutes % 30
        end_minutes += (30 - (end_minutes % 30)) % 30

        slots: list[str] = []
        current = start_minutes
        while current <= end_minutes:
            slots.append(f"{current // 60:02d}:{current % 60:02d}")
            current += 30
        return slots

    @staticmethod
    def _to_minutes(time_value: str) -> int | None:
        try:
            hours, minutes = time_value.split(":")
            return int(hours) * 60 + int(minutes)
        except (TypeError, ValueError):
            return None

    def _load_calendar_events(self) -> list[dict]:
        if self.calendar_client is None:
            return []

        if self.calendar_repo is None:
            try:
                return self.calendar_client.list_events(days=60)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudieron cargar eventos de Google Calendar")
                return []

        all_events: list[dict] = []
        calendars = self.calendar_repo.list_selected_calendars()
        for calendar_row in calendars:
            calendar_id = str(calendar_row["google_calendar_id"])
            try:
                events = self.calendar_client.list_events(calendar_id=calendar_id, days=60)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudieron cargar eventos del calendario %s", calendar_id)
                continue

            for event in events:
                enriched = dict(event)
                enriched["calendar_name"] = str(calendar_row["name"])
                enriched["background_color"] = str(calendar_row["background_color"])
                enriched["foreground_color"] = str(calendar_row["foreground_color"])
                enriched["google_calendar_id"] = calendar_id
                enriched["event_id"] = str(event.get("id") or "")
                all_events.append(enriched)

        return all_events

    def _group_entries_by_day(self, calendar_events: list[dict], notes_with_dates: list[Note]) -> dict[date, list[dict[str, str | int]]]:
        grouped: dict[date, list[dict[str, str | int]]] = {}

        for event in calendar_events:
            event_date = self._event_date(event)
            if event_date is None:
                continue
            grouped.setdefault(event_date, []).append(
                {
                    "origin": "EVENT",
                    "kind": "EVENT",
                    "title": str(event.get("summary") or "(Sin título)"),
                    "id": str(event.get("event_id") or event.get("id") or ""),
                    "status": "Pendiente",
                    "date": event_date.isoformat(),
                    "time": self._event_time(event),
                    "content": str(event.get("description") or ""),
                    "htmlLink": str(event.get("htmlLink") or ""),
                    "calendar_name": str(event.get("calendar_name") or ""),
                    "background_color": str(event.get("background_color") or EVENT_COLOR),
                    "foreground_color": _sanitize_tk_color(str(event.get("foreground_color") or "#000000")),
                    "google_calendar_id": str(event.get("google_calendar_id") or "primary"),
                }
            )

        for note in notes_with_dates:
            note_date = self._safe_parse_date(note.fecha)
            if note_date is None:
                continue
            kind = "EMAIL" if note.source == "email_pasted" else "NOTE"
            if (note.tipo or "").strip().lower() == "evento":
                kind = "EVENT"
            record = {
                "origin": kind,
                "kind": kind,
                "title": note.title or "(Nota sin título)",
                "id": note.id,
                "note_id": note.id,
                "status": note.estado or "Pendiente",
                "date": note.fecha or "",
                "time": note.hora_inicio or "",
                "time_end": note.hora_fin or "",
                "content": note.raw_text or "",
                "htmlLink": note.google_calendar_link or "",
                "google_calendar_id": note.google_calendar_id or "",
                "google_event_id": note.google_event_id or "",
                "created_at": note.created_at,
            }
            self._enrich_with_email_fields(record, note)
            grouped.setdefault(note_date, []).append(record)

        for day_items in grouped.values():
            day_items.sort(key=lambda item: (str(item.get("time") or "99:99"), str(item.get("title", "")).lower()))

        return grouped

    def _get_notes_with_date(self) -> list[Note]:
        notes_with_date: list[Note] = []
        for note in self.note_service.list_notes(limit=2000):
            if note.fecha:
                notes_with_date.append(note)
        return notes_with_date

    @staticmethod
    def _event_date(event: dict) -> date | None:
        start_value = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        if not start_value:
            return None
        return CalendarManagerWindow._safe_parse_date(str(start_value))

    @staticmethod
    def _event_time(event: dict) -> str:
        start_value = event.get("start", {}).get("dateTime")
        if not start_value:
            return ""
        try:
            normalized = str(start_value).replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).strftime("%H:%M")
        except ValueError:
            return ""

    @staticmethod
    def _safe_parse_date(value: str) -> date | None:
        if not value:
            return None
        try:
            if "T" in value:
                normalized = value.replace("Z", "+00:00")
                return datetime.fromisoformat(normalized).date()
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None

    @staticmethod
    def _month_matrix(year: int, month: int) -> list[date]:
        cal = calendar.Calendar(firstweekday=0)
        days: list[date] = []
        for week in cal.monthdatescalendar(year, month):
            days.extend(week)
        if len(days) < 42:
            last = days[-1]
            for add_days in range(1, 43 - len(days)):
                days.append(last + timedelta(days=add_days))
        return days[:42]

    @staticmethod
    def _entry_label_text(entry: dict[str, str | int], include_time: bool = True) -> str:
        title = str(entry.get("title") or "(Sin título)")
        time_value = str(entry.get("time") or "")
        if include_time and time_value:
            return f"{time_value} {title}"
        return title
