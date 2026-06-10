"""Dialog to manage master values by category."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from app.core.service import NOTION_DISABLED_MESSAGE, NoteService
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)


class MastersDialog(tk.Toplevel):
    """CRUD dialog for one master category, including descriptions."""

    def __init__(self, parent: tk.Misc, service: NoteService, category: str, on_change):
        super().__init__(parent)
        self.service = service
        self.category = category
        self.on_change = on_change
        self.selected_value = ""
        self.title(f"Gestionar {category}")
        apply_app_icon(self)
        self.geometry("720x500")
        self.transient(parent)
        self.grab_set()

        self.name_var = tk.StringVar()
        self.active_var = tk.StringVar(value="")
        self.locked_var = tk.StringVar(value="")

        self.tree = ttk.Treeview(self, columns=("value", "description", "active", "locked"), show="headings", height=12)
        self.tree.heading("value", text="Nombre")
        self.tree.heading("description", text="Descripción")
        self.tree.heading("active", text="Activo")
        self.tree.heading("locked", text="Bloqueado")
        self.tree.column("value", width=160)
        self.tree.column("description", width=320)
        self.tree.column("active", width=80)
        self.tree.column("locked", width=80)
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self._on_selected)

        form = ttk.LabelFrame(self, text="Detalle")
        form.pack(fill="x", padx=10, pady=(0, 10))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Nombre").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 4))
        ttk.Label(form, text="Descripción").grid(row=1, column=0, sticky="nw", padx=8, pady=(0, 8))
        self.description_text = tk.Text(form, height=4, wrap="word")
        self.description_text.grid(row=1, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Label(form, textvariable=self.active_var).grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(form, textvariable=self.locked_var).grid(row=2, column=1, sticky="w", padx=8, pady=(0, 8))

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(actions, text="Añadir", command=self._add).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Editar", command=self._edit).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Eliminar", command=self._deactivate).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Limpiar", command=self._clear_form).pack(side="left")
        ttk.Button(actions, text="Sincronizar con Notion", command=self._sync_schema).pack(side="right")

        self._refresh_rows()

    def _description(self) -> str:
        return self.description_text.get("1.0", "end").strip()

    def _set_description(self, value: str) -> None:
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", value)

    def _refresh_rows(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)

        for row in self.service.list_masters(self.category):
            value = str(row["value"])
            description = str(row["description"] or "")
            active = "Sí" if int(row["active"]) == 1 else "No"
            locked = "Sí" if int(row["system_locked"]) == 1 else "No"
            self.tree.insert("", "end", iid=value, values=(value, description, active, locked))

    def _on_selected(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        value = selected[0]
        rows = [row for row in self.service.list_masters(self.category) if str(row["value"]) == value]
        if not rows:
            return
        row = rows[0]
        self.selected_value = value
        self.name_var.set(value)
        self._set_description(str(row["description"] or ""))
        self.active_var.set("Activo: Sí" if int(row["active"]) == 1 else "Activo: No")
        self.locked_var.set("Bloqueado: Sí" if int(row["system_locked"]) == 1 else "Bloqueado: No")

    def _clear_form(self) -> None:
        self.selected_value = ""
        self.name_var.set("")
        self._set_description("")
        self.active_var.set("")
        self.locked_var.set("")
        self.tree.selection_remove(self.tree.selection())

    def _add(self) -> None:
        value = self.name_var.get().strip()
        if not value:
            messagebox.showwarning("Validación", "Debes ingresar un nombre.")
            return

        try:
            self.service.add_master(self.category, value, self._description())
            self._clear_form()
            self._refresh_rows()
            self.on_change()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo añadir maestro")
            messagebox.showerror("Error", "No se pudo añadir el maestro.")

    def _edit(self) -> None:
        if not self.selected_value:
            messagebox.showwarning("Atención", "Selecciona un valor para editar.")
            return
        try:
            self.service.update_master(self.category, self.selected_value, self.name_var.get(), self._description())
            updated_value = self.name_var.get().strip()
            self.selected_value = updated_value
            self._refresh_rows()
            self.tree.selection_set(updated_value)
            self.on_change()
        except ValueError as exc:
            messagebox.showwarning("Operación bloqueada", str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo editar maestro")
            messagebox.showerror("Error", "No se pudo editar el maestro.")

    def _deactivate(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Atención", "Selecciona un valor para eliminar.")
            return

        value = selected[0]
        try:
            self.service.deactivate_master(self.category, value)
            self._clear_form()
            self._refresh_rows()
            self.on_change()
        except ValueError as exc:
            messagebox.showwarning("Operación bloqueada", str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo desactivar maestro")
            messagebox.showerror("Error", "No se pudo eliminar el maestro.")

    def _sync_schema(self) -> None:
        if not self.service.is_notion_enabled():
            logger.info("NOTION_INTEGRATION: skipped sync because disabled")
            messagebox.showinfo("Notion desactivado", NOTION_DISABLED_MESSAGE)
            return

        try:
            self.service.sync_schema_with_notion()
            messagebox.showinfo("OK", "Esquema de Notion sincronizado correctamente.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error sincronizando esquema con Notion")
            messagebox.showerror("Error", str(exc))
