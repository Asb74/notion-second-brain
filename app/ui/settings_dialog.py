"""Tkinter dialog for app settings."""

from __future__ import annotations

import tkinter as tk
from typing import Callable
from tkinter import messagebox, ttk

from app.core.models import AppSettings
from app.ui.app_icons import apply_app_icon
from app.ui.dictation_widgets import attach_dictation


class SettingsDialog(tk.Toplevel):
    """Modal settings editor grouped by domain tabs."""

    def __init__(
        self,
        parent: tk.Misc,
        current: AppSettings,
        on_save: callable,
        on_open_master: Callable[[str], None] | None = None,
        initial_tab: str = "General",
    ):
        super().__init__(parent)
        self.title("Configuración")
        apply_app_icon(self)
        self.resizable(True, True)
        self.geometry("820x620")
        self.minsize(760, 540)
        self.transient(parent)
        self.grab_set()

        self._current = current
        self._on_save = on_save
        self._on_open_master = on_open_master

        self.entries: dict[str, ttk.Entry] = {}
        self.notification_vars = {
            "email_new": tk.BooleanVar(value=True),
            "daily_summary": tk.BooleanVar(value=False),
            "ml_alerts": tk.BooleanVar(value=True),
        }

        self.auto_check_var = tk.BooleanVar(value=True)
        self.interval_var = tk.IntVar(value=60)
        self.notifications_var = tk.BooleanVar(value=True)
        self.areas_list: tk.Listbox | None = None
        self.tipos_list: tk.Listbox | None = None

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 6))

        self.tab_general = ttk.Frame(self.notebook, padding=10)
        self.tab_email = ttk.Frame(self.notebook, padding=10)
        self.tab_notifications = ttk.Frame(self.notebook, padding=10)
        self.tab_master_data = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.tab_general, text="General")
        self.notebook.add(self.tab_email, text="Email")
        self.notebook.add(self.tab_notifications, text="Notificaciones")
        self.notebook.add(self.tab_master_data, text="Datos maestros")

        self._build_general_tab()
        self._build_email_tab()
        self._build_notifications_tab()
        self._build_master_data_tab()

        ttk.Button(self, text="Guardar", command=self._save).pack(anchor="e", padx=10, pady=(0, 10))

        tab_map = {
            "general": 0,
            "email": 1,
            "notificaciones": 2,
            "datos maestros": 3,
        }
        self.notebook.select(tab_map.get(initial_tab.lower().strip(), 0))

    def _build_general_tab(self) -> None:
        self._build_settings_fields(
            self.tab_general,
            fields=[
                ("notion_token", "Notion Token"),
                ("notion_database_id", "Notion Database ID"),
                ("prop_title", "Propiedad título"),
                ("prop_area", "Propiedad área"),
                ("prop_tipo", "Propiedad tipo"),
                ("prop_estado", "Propiedad estado"),
                ("prop_fecha", "Propiedad fecha"),
                ("prop_prioridad", "Propiedad prioridad"),
            ],
        )

    def _build_email_tab(self) -> None:
        frame = ttk.Frame(self.tab_email)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        self._build_settings_fields(
            frame,
            fields=[
                ("managed_email", "Correo gestionado"),
                ("default_area", "Área por defecto"),
                ("default_tipo", "Tipo por defecto"),
                ("default_estado", "Estado por defecto"),
                ("default_prioridad", "Prioridad por defecto"),
            ],
        )

        extra_row = 5
        ttk.Label(frame, text="Revisión automática").grid(row=extra_row, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(frame, variable=self.auto_check_var).grid(row=extra_row, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(frame, text="Intervalo (segundos)").grid(row=extra_row + 1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(frame, textvariable=self.interval_var, width=12).grid(row=extra_row + 1, column=1, sticky="w", padx=6, pady=4)

    def _build_settings_fields(self, parent: ttk.Frame, fields: list[tuple[str, str]]) -> None:
        parent.columnconfigure(1, weight=1)
        for idx, (key, label) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=idx, column=0, sticky="w", padx=6, pady=4)
            entry = ttk.Entry(parent, width=56, show="*" if key == "notion_token" else "")
            entry.insert(0, str(getattr(self._current, key, "") or ""))
            entry.grid(row=idx, column=1, sticky="ew", padx=6, pady=4)
            self.entries[key] = entry
            controls = attach_dictation(entry, self)
            controls.grid(row=idx, column=2, sticky="w", padx=(0, 6), pady=4)

    def _build_notifications_tab(self) -> None:
        frame = ttk.Frame(self.tab_notifications)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="Notificaciones del sistema",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(4, 8))

        ttk.Label(frame, text="Activar notificaciones").grid(row=0, column=1, sticky="w", padx=6, pady=(4, 8))
        ttk.Checkbutton(frame, variable=self.notifications_var).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(frame, text="Nuevos emails descargados", variable=self.notification_vars["email_new"]).grid(
            row=1,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )
        ttk.Checkbutton(frame, text="Resumen diario", variable=self.notification_vars["daily_summary"]).grid(
            row=2,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )
        ttk.Checkbutton(frame, text="Alertas de ML", variable=self.notification_vars["ml_alerts"]).grid(
            row=3,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )

        ttk.Label(
            frame,
            text="Estas opciones afectan solo avisos de UI y no cambian reglas de negocio.",
            foreground="#4b5563",
        ).grid(row=4, column=0, sticky="w", padx=6, pady=(10, 0))

    def _build_master_data_tab(self) -> None:
        frame = ttk.Frame(self.tab_master_data)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="Datos maestros de negocio", font=("TkDefaultFont", 10, "bold")).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            padx=6,
            pady=(4, 8),
        )
        ttk.Label(
            frame,
            text="Administra solo catálogos de negocio (Áreas, Tipos, Estados, Prioridades).",
            foreground="#4b5563",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 10))

        ttk.Label(frame, text="Áreas").grid(row=2, column=0, sticky="w", padx=6, pady=(0, 4))
        self.areas_list = tk.Listbox(frame, height=5)
        self.areas_list.grid(row=3, column=0, sticky="nsew", padx=6, pady=(0, 10))

        ttk.Label(frame, text="Tipos").grid(row=2, column=1, sticky="w", padx=6, pady=(0, 4))
        self.tipos_list = tk.Listbox(frame, height=5)
        self.tipos_list.grid(row=3, column=1, sticky="nsew", padx=6, pady=(0, 10))

        for value in self._seed_master_values("Area"):
            self.areas_list.insert(tk.END, value)
        for value in self._seed_master_values("Tipo"):
            self.tipos_list.insert(tk.END, value)

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        for label, category in [
            ("Áreas", "Area"),
            ("Tipos", "Tipo"),
            ("Estados", "Estado"),
            ("Prioridades", "Prioridad"),
        ]:
            ttk.Button(
                actions,
                text=label,
                command=lambda cat=category: self._open_master(cat),
            ).pack(side="left", padx=(0, 6))

    def _seed_master_values(self, key: str) -> list[str]:
        current_value = ""
        if key == "Area":
            current_value = self._current.default_area
        elif key == "Tipo":
            current_value = self._current.default_tipo
        value = str(current_value or "").strip()
        if not value:
            return []
        return [value]

    def _open_master(self, category: str) -> None:
        if self._on_open_master is None:
            messagebox.showinfo("Datos maestros", "Esta acción no está disponible en este contexto.", parent=self)
            return
        self._on_open_master(category)

    def _save(self) -> None:
        notion_token = self.entries["notion_token"].get().strip()
        if not notion_token:
            messagebox.showwarning("Validación", "El Notion Token no puede estar vacío.", parent=self)
            self.entries["notion_token"].focus_set()
            return

        new_settings = AppSettings(
            notion_token=notion_token,
            notion_database_id=self.entries["notion_database_id"].get().strip(),
            managed_email=self.entries["managed_email"].get().strip(),
            default_area=self.entries["default_area"].get().strip(),
            default_tipo=self.entries["default_tipo"].get().strip(),
            default_estado=self.entries["default_estado"].get().strip() or "Pendiente",
            default_prioridad=self.entries["default_prioridad"].get().strip() or "Media",
            prop_title=self.entries["prop_title"].get().strip() or "Actividad",
            prop_area=self.entries["prop_area"].get().strip() or "Area",
            prop_tipo=self.entries["prop_tipo"].get().strip() or "Tipo",
            prop_estado=self.entries["prop_estado"].get().strip() or "Estado",
            prop_fecha=self.entries["prop_fecha"].get().strip() or "Fecha",
            prop_prioridad=self.entries["prop_prioridad"].get().strip() or "Prioridad",
            max_attempts=self._current.max_attempts,
            retry_delay_seconds=self._current.retry_delay_seconds,
        )
        self._on_save(new_settings)
        self.destroy()
