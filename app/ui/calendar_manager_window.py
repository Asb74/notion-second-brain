"""Calendar agenda view for Google Calendar events and dated notes."""

from __future__ import annotations

import logging
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from app.core.calendar.google_calendar_client import GoogleCalendarClient
from app.core.service import NoteService

logger = logging.getLogger(__name__)


class CalendarManagerWindow(ttk.Frame):
    """Agenda frame that lists calendar and note entries."""

    def __init__(self, master: tk.Misc, note_service: NoteService):
        super().__init__(master, padding=10)
        self.note_service = note_service
        self.calendar_client: GoogleCalendarClient | None = None
        self._item_metadata: dict[str, dict[str, str]] = {}
        self._range_days = 30

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))

        self._build_toolbar()
        self._build_events_tree()
        self._initialize_client()
        self.load_events()

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 8))

        buttons = [
            ("Hoy", self._show_today),
            ("Semana", self._show_week),
            ("Mes", self._show_month),
            ("Actualizar", self.load_events),
        ]

        for idx, (label, command) in enumerate(buttons):
            ttk.Button(toolbar, text=label, command=command, style="Toolbar.TButton").grid(
                row=0,
                column=idx,
                sticky="ew",
                padx=(0, 6 if idx < len(buttons) - 1 else 0),
            )
            toolbar.columnconfigure(idx, weight=1)

    def _build_events_tree(self) -> None:
        columns = ("hour", "title", "origin")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=18)
        self.tree.heading("hour", text="Hora")
        self.tree.heading("title", text="Título")
        self.tree.heading("origin", text="Origen")
        self.tree.column("hour", width=180, anchor="w")
        self.tree.column("title", width=600, anchor="w")
        self.tree.column("origin", width=120, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)

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

    def _show_today(self) -> None:
        self._range_days = 1
        self.load_events()

    def _show_week(self) -> None:
        self._range_days = 7
        self.load_events()

    def _show_month(self) -> None:
        self._range_days = 30
        self.load_events()

    def load_events(self) -> None:
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        self._item_metadata.clear()

        events: list[dict] = []
        if self.calendar_client is not None:
            try:
                events = self.calendar_client.list_events(days=self._range_days)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudieron cargar eventos de Google Calendar")
                messagebox.showerror("Agenda", "No se pudo conectar con Google Calendar")

        for event in events:
            event_id = event.get("id", "")
            summary = event.get("summary", "(Sin título)")
            start_value = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date") or ""
            row_id = self.tree.insert("", "end", values=(self._format_hour(start_value), summary, "CALENDAR"))
            if event_id:
                self._item_metadata[row_id] = {
                    "origin": "CALENDAR",
                    "event_id": event_id,
                }

        for note in self._get_notes_in_range(self._range_days):
            row_id = self.tree.insert("", "end", values=(note["hour"], note["title"], "NOTE"))
            self._item_metadata[row_id] = {"origin": "NOTE", "note_id": str(note["id"])}

    def _get_notes_in_range(self, days: int) -> list[dict[str, str | int]]:
        start = date.today()
        end = start + timedelta(days=days)

        notes_in_range: list[dict[str, str | int]] = []
        for note in self.note_service.list_notes(limit=500):
            if not note.fecha:
                continue
            try:
                note_date = datetime.fromisoformat(note.fecha).date()
            except ValueError:
                continue
            if not (start <= note_date <= end):
                continue
            notes_in_range.append(
                {
                    "id": note.id,
                    "hour": f"{note_date.isoformat()} 00:00",
                    "title": note.title or "(Nota sin título)",
                }
            )

        notes_in_range.sort(key=lambda item: str(item["hour"]))
        return notes_in_range

    @staticmethod
    def _format_hour(value: str) -> str:
        if not value:
            return ""
        if "T" in value:
            return value.replace("T", " ").replace("Z", "")
        return f"{value} 00:00"

    def _on_double_click(self, _event: tk.Event) -> None:
        selected = self.tree.focus()
        if not selected:
            return

        item_data = self._item_metadata.get(selected, {})
        if item_data.get("origin") != "CALENDAR":
            return

        event_id = item_data.get("event_id", "")
        if not event_id:
            return

        webbrowser.open(f"https://calendar.google.com/calendar/r/eventedit/{event_id}")
