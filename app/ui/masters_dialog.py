"""Dialog to manage master values by category."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from app.core.service import NoteService

logger = logging.getLogger(__name__)


class MastersDialog(tk.Toplevel):
    """Simple CRUD dialog for one master category."""

    def __init__(self, parent: tk.Misc, service: NoteService, category: str, on_change):
        super().__init__(parent)
        self.service = service
        self.category = category
        self.on_change = on_change
        self.title(f"Gestionar {category}")
        self.geometry("540x420")
        self.transient(parent)
        self.grab_set()

        self.new_value_var = tk.StringVar()

        self.tree = ttk.Treeview(self, columns=("value", "active", "locked"), show="headings", height=12)
        self.tree.heading("value", text="Valor")
        self.tree.heading("active", text="Activo")
        self.tree.heading("locked", text="Bloqueado")
        self.tree.column("value", width=260)
        self.tree.column("active", width=90)
        self.tree.column("locked", width=90)
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Entry(form, textvariable=self.new_value_var).pack(side="left", fill="x", expand=True)
        ttk.Button(form, text="Añadir nuevo", command=self._add).pack(side="left", padx=6)
        ttk.Button(form, text="Desactivar", command=self._deactivate).pack(side="left", padx=6)

        ttk.Button(self, text="Sincronizar con Notion", command=self._sync_schema).pack(anchor="e", padx=10, pady=(0, 10))

        self._refresh_rows()

    def _refresh_rows(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)

        for row in self.service.list_masters(self.category):
            value = str(row["value"])
            active = "Sí" if int(row["active"]) == 1 else "No"
            locked = "Sí" if int(row["system_locked"]) == 1 else "No"
            self.tree.insert("", "end", iid=value, values=(value, active, locked))

    def _add(self) -> None:
        value = self.new_value_var.get().strip()
        if not value:
            messagebox.showwarning("Validación", "Debes ingresar un valor.")
            return

        try:
            self.service.add_master(self.category, value)
            self.new_value_var.set("")
            self._refresh_rows()
            self.on_change()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo añadir maestro")
            messagebox.showerror("Error", "No se pudo añadir el maestro.")

    def _deactivate(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Atención", "Selecciona un valor para desactivar.")
            return

        value = selected[0]
        try:
            self.service.deactivate_master(self.category, value)
            self._refresh_rows()
            self.on_change()
        except ValueError as exc:
            messagebox.showwarning("Operación bloqueada", str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo desactivar maestro")
            messagebox.showerror("Error", "No se pudo desactivar el maestro.")

    def _sync_schema(self) -> None:
        try:
            self.service.sync_schema_with_notion()
            messagebox.showinfo("OK", "Esquema de Notion sincronizado correctamente.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error sincronizando esquema con Notion")
            messagebox.showerror("Error", str(exc))
