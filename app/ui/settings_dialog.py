"""Tkinter dialog for app settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app.core.models import AppSettings


class SettingsDialog(tk.Toplevel):
    """Modal settings editor."""

    def __init__(self, parent: tk.Misc, current: AppSettings, on_save: callable):
        super().__init__(parent)
        self.title("Configuración")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._current = current
        self._on_save = on_save

        self.entries: dict[str, ttk.Entry] = {}
        fields = [
            ("notion_token", "Notion Token"),
            ("notion_database_id", "Notion Database ID"),
            ("default_area", "Área por defecto"),
            ("default_tipo", "Tipo por defecto"),
            ("default_estado", "Estado por defecto"),
            ("default_prioridad", "Prioridad por defecto"),
            ("prop_title", "Propiedad título"),
            ("prop_area", "Propiedad área"),
            ("prop_tipo", "Propiedad tipo"),
            ("prop_estado", "Propiedad estado"),
            ("prop_fecha", "Propiedad fecha"),
            ("prop_prioridad", "Propiedad prioridad"),
        ]

        for idx, (key, label) in enumerate(fields):
            ttk.Label(self, text=label).grid(row=idx, column=0, sticky="w", padx=6, pady=4)
            entry = ttk.Entry(self, width=50, show="*" if key == "notion_token" else "")
            entry.insert(0, str(getattr(current, key, "") or ""))
            entry.grid(row=idx, column=1, sticky="ew", padx=6, pady=4)
            self.entries[key] = entry

        ttk.Button(self, text="Guardar", command=self._save).grid(
            row=len(fields), column=1, sticky="e", padx=6, pady=8
        )

    def _save(self) -> None:
        notion_token = self.entries["notion_token"].get().strip()
        if not notion_token:
            messagebox.showwarning("Validación", "El Notion Token no puede estar vacío.", parent=self)
            self.entries["notion_token"].focus_set()
            return

        new_settings = AppSettings(
            notion_token=notion_token,
            notion_database_id=self.entries["notion_database_id"].get().strip(),
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
