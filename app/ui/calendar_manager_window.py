"""Calendar agenda view for Google Calendar events and dated notes."""

from __future__ import annotations

import calendar
import logging
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from app.core.calendar.google_calendar_client import GoogleCalendarClient
from app.core.models import Note
from app.core.service import NoteService

logger = logging.getLogger(__name__)


class CalendarManagerWindow(ttk.Frame):
    """Agenda frame with a monthly calendar view for events and notes."""

    def __init__(self, master: tk.Misc, note_service: NoteService):
        super().__init__(master, padding=10)
        self.note_service = note_service
        self.calendar_client: GoogleCalendarClient | None = None
        self.current_month = date.today().replace(day=1)
        self._label_metadata: dict[str, dict[str, str | int]] = {}
        self._events_by_day: dict[date, list[dict[str, str | int]]] = {}

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))
        style.configure("CalendarHeader.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("Weekday.TLabel", anchor="center", font=("TkDefaultFont", 9, "bold"))
        style.configure("DayNumber.TLabel", font=("TkDefaultFont", 9, "bold"))
        style.configure("Item.TLabel", foreground="#1f2937")
        style.configure("MoreItems.TLabel", foreground="#4b5563")

        self._build_toolbar()
        self._build_calendar_grid()
        self._initialize_client()
        self._refresh_data()

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Button(toolbar, text="Anterior", command=self._go_previous_month, style="Toolbar.TButton").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 6),
        )
        ttk.Button(toolbar, text="Hoy", command=self._go_today, style="Toolbar.TButton").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(0, 6),
        )
        ttk.Button(toolbar, text="Siguiente", command=self._go_next_month, style="Toolbar.TButton").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(0, 6),
        )

        self.month_label = ttk.Label(toolbar, text="", style="CalendarHeader.TLabel")
        self.month_label.grid(row=0, column=3, sticky="ew")

        ttk.Button(toolbar, text="Actualizar", command=self._refresh_data, style="Toolbar.TButton").grid(
            row=0,
            column=4,
            sticky="e",
        )

        toolbar.columnconfigure(3, weight=1)

    def _build_calendar_grid(self) -> None:
        container = ttk.Frame(self)
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
                cell = tk.Frame(
                    container,
                    bg="#ffffff",
                    highlightbackground="#d1d5db",
                    highlightthickness=1,
                    bd=0,
                    padx=6,
                    pady=6,
                )
                cell.grid(row=week + 1, column=day, sticky="nsew", padx=2, pady=2)
                cell.grid_propagate(False)

                day_label = ttk.Label(cell, text="", style="DayNumber.TLabel")
                day_label.pack(anchor="nw")

                events_frame = ttk.Frame(cell)
                events_frame.pack(fill="both", expand=True, pady=(4, 0))

                self.cells.append(cell)
                self.day_number_labels.append(day_label)
                self.day_events_frames.append(events_frame)

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
            logger.exception("No se pudo conectar con Google Calendar")
            self.calendar_client = None
            messagebox.showerror("Agenda", "No se pudo conectar con Google Calendar")

    def _refresh_data(self) -> None:
        self._events_by_day = self._load_entries_by_day()
        self._render_month()

    def _go_previous_month(self) -> None:
        year = self.current_month.year
        month = self.current_month.month - 1
        if month < 1:
            month = 12
            year -= 1
        self.current_month = date(year, month, 1)
        self._render_month()

    def _go_next_month(self) -> None:
        year = self.current_month.year
        month = self.current_month.month + 1
        if month > 12:
            month = 1
            year += 1
        self.current_month = date(year, month, 1)
        self._render_month()

    def _go_today(self) -> None:
        self.current_month = date.today().replace(day=1)
        self._render_month()

    def _render_month(self) -> None:
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
                label = ttk.Label(events_frame, text=text, style="Item.TLabel", cursor="hand2", wraplength=170)
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Double-1>", self._on_entry_double_click)
                self._label_metadata[str(label)] = entry

            hidden_count = max(0, len(day_entries) - 3)
            if hidden_count:
                ttk.Label(events_frame, text=f"+{hidden_count} más...", style="MoreItems.TLabel").pack(
                    anchor="w",
                    pady=(2, 0),
                )

    def _load_entries_by_day(self) -> dict[date, list[dict[str, str | int]]]:
        grouped: dict[date, list[dict[str, str | int]]] = {}

        if self.calendar_client is not None:
            try:
                events = self.calendar_client.list_events(days=60)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudieron cargar eventos de Google Calendar")
                messagebox.showerror("Agenda", "No se pudo conectar con Google Calendar")
                events = []

            for event in events:
                event_date = self._event_date(event)
                if event_date is None:
                    continue
                grouped.setdefault(event_date, []).append(
                    {
                        "origin": "CALENDAR",
                        "title": str(event.get("summary") or "(Sin título)"),
                        "event_id": str(event.get("id") or ""),
                    }
                )

        for note in self._get_notes_with_date():
            note_date = self._safe_parse_date(note.fecha)
            if note_date is None:
                continue
            grouped.setdefault(note_date, []).append(
                {
                    "origin": "NOTE",
                    "title": note.title or "(Nota sin título)",
                    "note_id": note.id,
                }
            )

        for day_items in grouped.values():
            day_items.sort(key=lambda item: str(item.get("title", "")).lower())

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
        cal = calendar.Calendar(firstweekday=0)  # Monday
        days: list[date] = []
        for week in cal.monthdatescalendar(year, month):
            days.extend(week)
        if len(days) < 42:
            last = days[-1]
            for add_days in range(1, 43 - len(days)):
                days.append(last + timedelta(days=add_days))
        return days[:42]

    @staticmethod
    def _entry_label_text(entry: dict[str, str | int]) -> str:
        if entry.get("origin") == "NOTE":
            return f"📌 Nota: {entry.get('title', '(Nota sin título)')}"
        return f"📅 Evento: {entry.get('title', '(Sin título)')}"

    def _on_entry_double_click(self, event: tk.Event) -> None:
        widget = event.widget
        entry_data = self._label_metadata.get(str(widget), {})
        if not entry_data:
            return

        if entry_data.get("origin") == "CALENDAR":
            event_id = str(entry_data.get("event_id") or "")
            if not event_id:
                return
            webbrowser.open(f"https://calendar.google.com/calendar/r/eventedit/{event_id}")
            return

        note_id = entry_data.get("note_id")
        if not isinstance(note_id, int):
            return

        note = self.note_service.note_repo.get_note(note_id)
        if not note or not note.notion_page_id:
            messagebox.showwarning("Atención", "No hay página Notion asociada.")
            return

        notion_url = f"https://www.notion.so/{note.notion_page_id.replace('-', '')}"
        webbrowser.open(notion_url)
