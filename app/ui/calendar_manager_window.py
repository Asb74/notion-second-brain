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
        self.current_date = date.today()
        self.current_month = self.current_date.replace(day=1)
        self.view_mode = "month"
        self.calendar_events: list[dict] = []
        self.notes_with_dates: list[Note] = []
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
        style.configure("Time.TLabel", foreground="#4b5563")

        self._build_toolbar()
        self.calendar_body = ttk.Frame(self)
        self.calendar_body.pack(fill="both", expand=True)
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

        ttk.Button(toolbar, text="Día", command=self._set_view_day, style="Toolbar.TButton").grid(
            row=0,
            column=3,
            sticky="w",
            padx=(8, 4),
        )
        ttk.Button(toolbar, text="Semana", command=self._set_view_week, style="Toolbar.TButton").grid(
            row=0,
            column=4,
            sticky="w",
            padx=4,
        )
        ttk.Button(toolbar, text="Mes", command=self._set_view_month, style="Toolbar.TButton").grid(
            row=0,
            column=5,
            sticky="w",
            padx=(4, 10),
        )

        self.month_label = ttk.Label(toolbar, text="", style="CalendarHeader.TLabel")
        self.month_label.grid(row=0, column=6, sticky="ew")

        ttk.Button(toolbar, text="Actualizar", command=self._refresh_data, style="Toolbar.TButton").grid(
            row=0,
            column=7,
            sticky="e",
        )

        toolbar.columnconfigure(6, weight=1)

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
        self.calendar_events = self._load_calendar_events()
        self.notes_with_dates = self._get_notes_with_date()
        self._events_by_day = self._group_entries_by_day(self.calendar_events, self.notes_with_dates)
        self.render_calendar()

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

    def _render_week_view(self) -> None:
        self._clear_calendar_body()
        self._label_metadata.clear()

        container = ttk.Frame(self.calendar_body)
        container.pack(fill="both", expand=True)

        week_start = self.current_date - timedelta(days=self.current_date.weekday())
        week_end = week_start + timedelta(days=6)
        self.month_label.configure(
            text=f"Semana {week_start.day}–{week_end.day} {week_end.strftime('%B %Y').capitalize()}"
        )

        weekday_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for index, day_name in enumerate(weekday_names):
            container.columnconfigure(index, weight=1, uniform="week-col")
            day_value = week_start + timedelta(days=index)

            column = tk.Frame(
                container,
                bg="#ffffff",
                highlightbackground="#d1d5db",
                highlightthickness=1,
                bd=0,
                padx=6,
                pady=6,
            )
            column.grid(row=0, column=index, sticky="nsew", padx=2, pady=2)

            ttk.Label(column, text=f"{day_name} {day_value.day}", style="Weekday.TLabel").pack(anchor="w")

            day_entries = self._events_by_day.get(day_value, [])
            for entry in day_entries:
                text = self._entry_label_text(entry)
                label = ttk.Label(column, text=text, style="Item.TLabel", cursor="hand2", wraplength=165)
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Double-1>", self._on_entry_double_click)
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

            slot_frame = tk.Frame(
                container,
                bg="#ffffff",
                highlightbackground="#d1d5db",
                highlightthickness=1,
                bd=0,
                padx=6,
                pady=4,
            )
            slot_frame.grid(row=row, column=1, sticky="nsew", pady=1)

            for entry in entries_by_slot.get(slot, []):
                text = self._entry_label_text(entry, include_time=False)
                label = ttk.Label(slot_frame, text=text, style="Item.TLabel", cursor="hand2", wraplength=500)
                label.pack(anchor="w", fill="x", pady=1)
                label.bind("<Double-1>", self._on_entry_double_click)
                self._label_metadata[str(label)] = entry

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
            messagebox.showerror("Agenda", "No se pudo conectar con Google Calendar")
            return []

    def _group_entries_by_day(
        self,
        calendar_events: list[dict],
        notes_with_dates: list[Note],
    ) -> dict[date, list[dict[str, str | int]]]:
        grouped: dict[date, list[dict[str, str | int]]] = {}

        for event in calendar_events:
            event_date = self._event_date(event)
            if event_date is None:
                continue
            grouped.setdefault(event_date, []).append(
                {
                    "origin": "CALENDAR",
                    "title": str(event.get("summary") or "(Sin título)"),
                    "event_id": str(event.get("id") or ""),
                    "time": self._event_time(event),
                }
            )

        for note in notes_with_dates:
            note_date = self._safe_parse_date(note.fecha)
            if note_date is None:
                continue
            grouped.setdefault(note_date, []).append(
                {
                    "origin": "NOTE",
                    "title": note.title or "(Nota sin título)",
                    "note_id": note.id,
                    "time": "",
                }
            )

        for day_items in grouped.values():
            day_items.sort(
                key=lambda item: (
                    str(item.get("time") or "99:99"),
                    str(item.get("title", "")).lower(),
                )
            )

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
    def _entry_label_text(entry: dict[str, str | int], include_time: bool = True) -> str:
        time_prefix = ""
        if include_time and entry.get("time"):
            time_prefix = f"{entry.get('time')} "
        if entry.get("origin") == "NOTE":
            return f"{time_prefix}📌 {entry.get('title', '(Nota sin título)')}"
        return f"{time_prefix}📅 {entry.get('title', '(Sin título)')}"

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
