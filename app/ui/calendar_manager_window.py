"""Calendar agenda view for Google Calendar events and dated notes."""

from __future__ import annotations

import calendar
import logging
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from app.core.calendar.google_calendar_client import GoogleCalendarClient
from app.core.models import Action, Note
from app.core.service import NoteService

logger = logging.getLogger(__name__)


class CalendarManagerWindow(ttk.Frame):
    """CRM-like agenda frame with overview on top and details below."""

    TYPE_LABELS = {
        "NOTE": "📌 nota",
        "ACTION": "✔ acción",
        "EMAIL": "📧 email",
        "EVENT": "📅 evento",
    }

    TYPE_COLORS = {
        "NOTE": "#dbeafe",
        "ACTION": "#fef9c3",
        "EMAIL": "#dcfce7",
        "EVENT": "#ffedd5",
    }

    PENDING_DARK = {
        "NOTE": "#1e3a8a",
        "ACTION": "#854d0e",
        "EMAIL": "#14532d",
        "EVENT": "#9a3412",
    }

    def __init__(self, master: tk.Misc, note_service: NoteService):
        super().__init__(master, padding=10)
        self.note_service = note_service
        self.calendar_client: GoogleCalendarClient | None = None
        self.current_date = date.today()
        self.current_month = self.current_date.replace(day=1)
        self.view_mode = "month"
        self.calendar_events: list[dict] = []
        self.notes_with_dates: list[Note] = []
        self.actions: list[Action] = []
        self._label_metadata: dict[str, dict[str, str | int]] = {}
        self._events_by_day: dict[date, list[dict[str, str | int]]] = {}
        self._overview_records: dict[str, dict[str, str | int]] = {}
        self._action_vars: dict[int, tk.BooleanVar] = {}
        self._selected_record: dict[str, str | int] | None = None

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))
        style.configure("CalendarHeader.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("Weekday.TLabel", anchor="center", font=("TkDefaultFont", 9, "bold"))
        style.configure("DayNumber.TLabel", font=("TkDefaultFont", 9, "bold"))
        style.configure("Item.TLabel", foreground="#1f2937")
        style.configure("MoreItems.TLabel", foreground="#4b5563")
        style.configure("Time.TLabel", foreground="#4b5563")

        self._build_toolbar()
        self._build_layout()
        self._initialize_client()
        self.refresh_overview()

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Button(toolbar, text="Anterior", command=self._go_previous_month, style="Toolbar.TButton").grid(row=0, column=0, padx=4)
        ttk.Button(toolbar, text="Hoy", command=self._go_today, style="Toolbar.TButton").grid(row=0, column=1, padx=4)
        ttk.Button(toolbar, text="Siguiente", command=self._go_next_month, style="Toolbar.TButton").grid(row=0, column=2, padx=4)

        ttk.Button(toolbar, text="Día", command=self._set_view_day, style="Toolbar.TButton").grid(row=0, column=3, padx=(10, 4))
        ttk.Button(toolbar, text="Semana", command=self._set_view_week, style="Toolbar.TButton").grid(row=0, column=4, padx=4)
        ttk.Button(toolbar, text="Mes", command=self._set_view_month, style="Toolbar.TButton").grid(row=0, column=5, padx=4)

        self.month_label = ttk.Label(toolbar, text="", style="CalendarHeader.TLabel")
        self.month_label.grid(row=0, column=6, sticky="ew", padx=8)

        ttk.Button(toolbar, text="Lista", command=self._show_list_overview, style="Toolbar.TButton").grid(row=0, column=7, padx=4)
        ttk.Button(toolbar, text="Calendario", command=self._show_calendar_overview, style="Toolbar.TButton").grid(row=0, column=8, padx=4)
        ttk.Button(toolbar, text="Actualizar", command=self.refresh_overview, style="Toolbar.TButton").grid(row=0, column=9, padx=4)

        toolbar.columnconfigure(6, weight=1)

    def _build_layout(self) -> None:
        self.crm_paned = ttk.PanedWindow(self, orient="vertical")
        self.crm_paned.pack(fill="both", expand=True)

        self.overview_panel = ttk.Frame(self.crm_paned)
        self.detail_panel = ttk.Frame(self.crm_paned, padding=8)
        self.crm_paned.add(self.overview_panel, weight=7)
        self.crm_paned.add(self.detail_panel, weight=3)

        self.overview_stack = ttk.Frame(self.overview_panel)
        self.overview_stack.pack(fill="both", expand=True)

        self.list_frame = ttk.Frame(self.overview_stack)
        self.calendar_frame = ttk.Frame(self.overview_stack)
        for frame in (self.list_frame, self.calendar_frame):
            frame.grid(row=0, column=0, sticky="nsew")
        self.overview_stack.rowconfigure(0, weight=1)
        self.overview_stack.columnconfigure(0, weight=1)

        self._build_overview_list()
        self.calendar_body = ttk.Frame(self.calendar_frame)
        self.calendar_body.pack(fill="both", expand=True)

        self._build_detail_panel()
        self._show_calendar_overview()

    def _build_overview_list(self) -> None:
        columns = ("kind", "title", "status", "date")
        self.overview_tree = ttk.Treeview(self.list_frame, columns=columns, show="headings", height=16)
        self.overview_tree.heading("kind", text="Tipo")
        self.overview_tree.heading("title", text="Título")
        self.overview_tree.heading("status", text="Estado")
        self.overview_tree.heading("date", text="Fecha")
        self.overview_tree.column("kind", width=140, anchor="w")
        self.overview_tree.column("title", width=480, anchor="w")
        self.overview_tree.column("status", width=130, anchor="center")
        self.overview_tree.column("date", width=120, anchor="center")
        self.overview_tree.pack(fill="both", expand=True)
        self.overview_tree.bind("<<TreeviewSelect>>", self._on_overview_select)

        self.overview_tree.tag_configure("NOTE_PENDING", background="#1e3a8a", foreground="#ffffff")
        self.overview_tree.tag_configure("ACTION_PENDING", background="#854d0e", foreground="#ffffff")
        self.overview_tree.tag_configure("EMAIL_PENDING", background="#14532d", foreground="#ffffff")
        self.overview_tree.tag_configure("EVENT_PENDING", background="#9a3412", foreground="#ffffff")
        self.overview_tree.tag_configure("NOTE_DONE", background="#cbd5d1", foreground="#374151")
        self.overview_tree.tag_configure("ACTION_DONE", background="#e5e7eb", foreground="#374151")
        self.overview_tree.tag_configure("EMAIL_DONE", background="#e5e7eb", foreground="#374151")
        self.overview_tree.tag_configure("EVENT_DONE", background="#e5e7eb", foreground="#374151")

    def _build_detail_panel(self) -> None:
        self.detail_title_var = tk.StringVar(value="Selecciona un registro")
        self.detail_type_var = tk.StringVar(value="-")
        self.detail_status_var = tk.StringVar(value="-")
        self.detail_date_var = tk.StringVar(value="-")

        ttk.Label(self.detail_panel, textvariable=self.detail_title_var, style="CalendarHeader.TLabel").pack(anchor="w")

        metadata = ttk.Frame(self.detail_panel)
        metadata.pack(fill="x", pady=(6, 8))
        ttk.Label(metadata, text="Tipo:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(metadata, textvariable=self.detail_type_var).grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Label(metadata, text="Estado:").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Label(metadata, textvariable=self.detail_status_var).grid(row=0, column=3, sticky="w", padx=(0, 20))
        ttk.Label(metadata, text="Fecha:").grid(row=0, column=4, sticky="w", padx=(0, 6))
        ttk.Label(metadata, textvariable=self.detail_date_var).grid(row=0, column=5, sticky="w")

        self.content_text = tk.Text(self.detail_panel, height=7, wrap="word")
        self.content_text.pack(fill="both", expand=True)

        self.associated_actions_frame = ttk.LabelFrame(self.detail_panel, text="Acciones asociadas")
        self.associated_actions_frame.pack(fill="x", pady=(8, 6))

        self.context_buttons = ttk.Frame(self.detail_panel)
        self.context_buttons.pack(fill="x", pady=(2, 0))

    def _show_list_overview(self) -> None:
        self.list_frame.tkraise()

    def _show_calendar_overview(self) -> None:
        self.calendar_frame.tkraise()

    def _refresh_data(self) -> None:
        self.calendar_events = self._load_calendar_events()
        self.notes_with_dates = self._get_notes_with_date()
        self.actions = self.note_service.list_actions(limit=3000)
        self._events_by_day = self._group_entries_by_day(self.calendar_events, self.notes_with_dates)

    def refresh_overview(self) -> None:
        self._refresh_data()
        self.render_calendar()
        self._refresh_overview_list()
        if self._selected_record:
            self.show_detail(self._selected_record)

    def _refresh_overview_list(self) -> None:
        self._overview_records.clear()
        for iid in self.overview_tree.get_children():
            self.overview_tree.delete(iid)

        for note in self.note_service.list_notes(limit=2000):
            record_type = "EMAIL" if note.source == "email_pasted" else "NOTE"
            record = {
                "kind": record_type,
                "id": note.id,
                "title": note.title or "(Nota sin título)",
                "status": note.estado or "Pendiente",
                "date": note.fecha or "",
                "content": note.raw_text or "",
            }
            self._insert_overview_record(record)

        for action in self.actions:
            record = {
                "kind": "ACTION",
                "id": action.id,
                "title": action.description or "(Acción sin descripción)",
                "status": action.status or "pendiente",
                "date": action.completed_at or action.created_at,
                "content": action.description or "",
                "note_id": action.note_id,
            }
            self._insert_overview_record(record)

        for event in self.calendar_events:
            event_date = self._event_date(event)
            record = {
                "kind": "EVENT",
                "id": str(event.get("id") or ""),
                "title": str(event.get("summary") or "(Sin título)"),
                "status": "Pendiente",
                "date": event_date.isoformat() if event_date else "",
                "content": str(event.get("description") or ""),
            }
            self._insert_overview_record(record)

    def _insert_overview_record(self, record: dict[str, str | int]) -> None:
        iid = f"{record['kind']}_{record['id']}"
        self._overview_records[iid] = record
        self.overview_tree.insert(
            "",
            "end",
            iid=iid,
            values=(
                self.TYPE_LABELS.get(str(record["kind"]), str(record["kind"])),
                str(record.get("title") or ""),
                str(record.get("status") or ""),
                str(record.get("date") or ""),
            ),
            tags=(self._record_tag(record),),
        )

    def _record_tag(self, record: dict[str, str | int]) -> str:
        status = str(record.get("status") or "").lower()
        kind = str(record.get("kind") or "NOTE")
        is_done = status in {"finalizado", "completado", "hecha", "done"}
        suffix = "DONE" if is_done else "PENDING"
        return f"{kind}_{suffix}"

    def _on_overview_select(self, _event: tk.Event) -> None:
        selected = self.overview_tree.selection()
        if not selected:
            return
        record = self._overview_records.get(selected[0])
        if record:
            self.show_detail(record)

    def show_detail(self, record: dict[str, str | int]) -> None:
        self._selected_record = record
        kind = str(record.get("kind") or "NOTE")

        self.detail_title_var.set(str(record.get("title") or "(Sin título)"))
        self.detail_type_var.set(self.TYPE_LABELS.get(kind, kind))
        self.detail_status_var.set(str(record.get("status") or "-"))
        self.detail_date_var.set(str(record.get("date") or "-"))

        self.content_text.configure(bg=self._detail_bg(record), fg="#111827")
        self.content_text.delete("1.0", "end")
        self.content_text.insert("1.0", str(record.get("content") or ""))

        self._render_associated_actions(record)
        self._render_context_actions(record)

    def _detail_bg(self, record: dict[str, str | int]) -> str:
        status = str(record.get("status") or "").lower()
        kind = str(record.get("kind") or "NOTE")
        if status in {"finalizado", "completado", "hecha", "done"}:
            return "#cbd5d1" if kind == "NOTE" else "#e5e7eb"
        return self.TYPE_COLORS.get(kind, "#ffffff")

    def _render_associated_actions(self, record: dict[str, str | int]) -> None:
        for child in self.associated_actions_frame.winfo_children():
            child.destroy()
        self._action_vars.clear()

        if str(record.get("kind")) not in {"NOTE", "EMAIL"}:
            ttk.Label(self.associated_actions_frame, text="No aplica para este registro.").pack(anchor="w", padx=6, pady=4)
            return

        note_id = int(record.get("id") or 0)
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
        var = self._action_vars.get(action_id)
        if var is None or not var.get():
            return
        self.note_service.mark_action_done(action_id)
        if self._selected_record and str(self._selected_record.get("kind")) in {"NOTE", "EMAIL"}:
            note_id = int(self._selected_record.get("id") or 0)
            note = self.note_service.get_note_by_id(note_id)
            if note:
                self._selected_record["status"] = note.estado
        self.refresh_overview()

    def _render_context_actions(self, record: dict[str, str | int]) -> None:
        for child in self.context_buttons.winfo_children():
            child.destroy()

        kind = str(record.get("kind") or "NOTE")
        if kind == "NOTE":
            ttk.Button(self.context_buttons, text="Editar", command=self._save_note_edits).pack(side="left", padx=4)
            ttk.Button(self.context_buttons, text="Completar", command=self._complete_current_note).pack(side="left", padx=4)
            ttk.Button(self.context_buttons, text="Crear acción", command=self._create_action_for_current_note).pack(side="left", padx=4)
        elif kind == "ACTION":
            ttk.Button(self.context_buttons, text="Marcar completada", command=self._complete_current_action).pack(side="left", padx=4)
            ttk.Button(self.context_buttons, text="Editar", command=self._save_action_edits).pack(side="left", padx=4)
        elif kind == "EMAIL":
            ttk.Button(self.context_buttons, text="Responder", command=lambda: logger.info("Responder email")).pack(side="left", padx=4)
            ttk.Button(self.context_buttons, text="Reenviar", command=lambda: logger.info("Reenviar email")).pack(side="left", padx=4)
            ttk.Button(self.context_buttons, text="Abrir email", command=lambda: logger.info("Abrir email")).pack(side="left", padx=4)
        elif kind == "EVENT":
            ttk.Button(self.context_buttons, text="Editar evento", command=self._edit_event).pack(side="left", padx=4)
            ttk.Button(self.context_buttons, text="Abrir en Google Calendar", command=self._open_event).pack(side="left", padx=4)

    def _save_note_edits(self) -> None:
        if not self._selected_record:
            return
        note_id = int(self._selected_record.get("id") or 0)
        title = self.detail_title_var.get().strip()
        content = self.content_text.get("1.0", "end").strip()
        self.note_service.update_note_content(note_id, title, content)
        self.refresh_overview()

    def _save_action_edits(self) -> None:
        if not self._selected_record:
            return
        action_id = int(self._selected_record.get("id") or 0)
        content = self.content_text.get("1.0", "end").strip()
        self.note_service.update_action_description(action_id, content)
        self.refresh_overview()

    def _create_action_for_current_note(self) -> None:
        if not self._selected_record:
            return
        note_id = int(self._selected_record.get("id") or 0)
        note = self.note_service.get_note_by_id(note_id)
        if not note:
            return
        description = self.content_text.get("1.0", "end").strip().splitlines()[0][:180] or "Nueva acción"
        self.note_service.actions_repo.create_action(note_id, description, note.area)
        self.refresh_overview()

    def _complete_current_note(self) -> None:
        if not self._selected_record:
            return
        note_id = int(self._selected_record.get("id") or 0)
        self.note_service.note_repo.update_estado(note_id, "Finalizado")
        self.refresh_overview()

    def _complete_current_action(self) -> None:
        if not self._selected_record:
            return
        action_id = int(self._selected_record.get("id") or 0)
        self.note_service.mark_action_done(action_id)
        self.refresh_overview()

    def _edit_event(self) -> None:
        self._open_event()

    def _open_event(self) -> None:
        if not self._selected_record:
            return
        event_id = str(self._selected_record.get("id") or "")
        if event_id:
            webbrowser.open(f"https://calendar.google.com/calendar/r/eventedit/{event_id}")

    def _initialize_client(self) -> None:
        credentials_path = Path(r"C:\notion-second-brain\secrets\calendar_credentials.json")
        token_path = Path(r"C:\notion-second-brain\secrets\calendar_token.json")

        if not credentials_path.exists():
            credentials_path = Path("secrets/calendar_credentials.json")
        if not token_path.parent.exists():
            token_path = Path("secrets/calendar_token.json")

        try:
            self.calendar_client = GoogleCalendarClient(str(credentials_path), str(token_path))
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
        for child in self.calendar_body.winfo_children():
            child.destroy()

    def render_calendar(self) -> None:
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
                cell.configure(bg="#f5f5f5")
                day_number_label.configure(text=str(day_value.day), foreground="#9ca3af")
                continue

            cell.configure(bg="#ffffff")
            today = date.today()
            if day_value == today:
                day_number_label.configure(text=str(day_value.day), foreground="#1d4ed8")
            else:
                day_number_label.configure(text=str(day_value.day), foreground="#111827")

            day_entries = self._events_by_day.get(day_value, [])
            for entry in day_entries[:3]:
                text = self._entry_label_text(entry)
                label = tk.Label(events_frame, text=text, anchor="w", justify="left", cursor="hand2", wraplength=170, bg=self._detail_bg(entry))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                self._label_metadata[str(label)] = entry

    def _build_month_grid(self) -> None:
        container = ttk.Frame(self.calendar_body)
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
                cell = tk.Frame(container, bg="#ffffff", highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=6, pady=6)
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

        container = ttk.Frame(self.calendar_body)
        container.pack(fill="both", expand=True)

        week_start = self.current_date - timedelta(days=self.current_date.weekday())
        week_end = week_start + timedelta(days=6)
        self.month_label.configure(text=f"Semana {week_start.day}–{week_end.day} {week_end.strftime('%B %Y').capitalize()}")

        weekday_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for index, day_name in enumerate(weekday_names):
            container.columnconfigure(index, weight=1, uniform="week-col")
            day_value = week_start + timedelta(days=index)

            column = tk.Frame(container, bg="#ffffff", highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=6, pady=6)
            column.grid(row=0, column=index, sticky="nsew", padx=2, pady=2)

            ttk.Label(column, text=f"{day_name} {day_value.day}", style="Weekday.TLabel").pack(anchor="w")

            day_entries = self._events_by_day.get(day_value, [])
            for entry in day_entries:
                text = self._entry_label_text(entry)
                label = tk.Label(column, text=text, anchor="w", justify="left", cursor="hand2", wraplength=165, bg=self._detail_bg(entry))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                self._label_metadata[str(label)] = entry

    def _render_day_view(self) -> None:
        self._clear_calendar_body()
        self._label_metadata.clear()
        self.month_label.configure(text=self.current_date.strftime("%A %d %B %Y").capitalize())

        container = ttk.Frame(self.calendar_body)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        entries = self._events_by_day.get(self.current_date, [])
        slots = self._time_slots(entries)

        entries_by_slot: dict[str, list[dict[str, str | int]]] = {}
        for entry in entries:
            entry_time = str(entry.get("time") or "")
            if entry_time:
                entries_by_slot.setdefault(entry_time, []).append(entry)

        for row, slot in enumerate(slots):
            time_label = ttk.Label(container, text=slot, style="Time.TLabel")
            time_label.grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=1)

            slot_frame = tk.Frame(container, bg="#ffffff", highlightbackground="#d1d5db", highlightthickness=1, bd=0, padx=6, pady=4)
            slot_frame.grid(row=row, column=1, sticky="nsew", pady=1)

            for entry in entries_by_slot.get(slot, []):
                text = self._entry_label_text(entry, include_time=False)
                label = tk.Label(slot_frame, text=text, anchor="w", justify="left", cursor="hand2", wraplength=500, bg=self._detail_bg(entry))
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Button-1>", self._on_entry_click)
                self._label_metadata[str(label)] = entry

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

        try:
            return self.calendar_client.list_events(days=60)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron cargar eventos de Google Calendar")
            return []

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
                    "id": str(event.get("id") or ""),
                    "status": "Pendiente",
                    "date": event_date.isoformat(),
                    "time": self._event_time(event),
                    "content": str(event.get("description") or ""),
                }
            )

        for note in notes_with_dates:
            note_date = self._safe_parse_date(note.fecha)
            if note_date is None:
                continue
            kind = "EMAIL" if note.source == "email_pasted" else "NOTE"
            grouped.setdefault(note_date, []).append(
                {
                    "origin": kind,
                    "kind": kind,
                    "title": note.title or "(Nota sin título)",
                    "id": note.id,
                    "status": note.estado or "Pendiente",
                    "date": note.fecha or "",
                    "time": "",
                    "content": note.raw_text or "",
                }
            )

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
        time_prefix = f"{entry.get('time')} " if include_time and entry.get("time") else ""
        kind = str(entry.get("kind") or "NOTE")
        icon = CalendarManagerWindow.TYPE_LABELS.get(kind, kind).split()[0]
        return f"{time_prefix}{icon} {entry.get('title', '(Sin título)')}"
