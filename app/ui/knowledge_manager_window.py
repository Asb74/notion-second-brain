"""Tkinter window for the Knowledge Manager module."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.persistence.knowledge_repository import KnowledgeRepository
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)


class KnowledgeManagerWindow(tk.Toplevel):
    """Basic CRUD interface for manual and sourced knowledge notes."""

    def __init__(self, parent: tk.Misc, db_connection: sqlite3.Connection):
        super().__init__(parent)
        self.repo = KnowledgeRepository(db_connection)
        self.current_item_id: int | None = None
        self.areas_by_name: dict[str, int | None] = {}
        self.types_by_name: dict[str, int | None] = {}

        self.title("Knowledge Manager")
        apply_app_icon(self)
        self.geometry("1180x760")
        self.minsize(980, 620)
        logger.info("KNOWLEDGE: módulo abierto")

        self.search_var = tk.StringVar()
        self.area_filter_var = tk.StringVar(value="Todas")
        self.type_filter_var = tk.StringVar(value="Todos")
        self.title_var = tk.StringVar()
        self.area_var = tk.StringVar()
        self.type_var = tk.StringVar()
        self.tags_var = tk.StringVar()
        self.source_var = tk.StringVar(value="manual")
        self.status_var = tk.StringVar(value="Listo")

        self._build_layout()
        self._load_reference_values()
        self.refresh_items()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=2)
        paned.add(right, weight=3)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(5, weight=3)
        right.rowconfigure(6, weight=1)

        ttk.Label(left, text="Buscar").grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(left, textvariable=self.search_var)
        search_entry.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        search_entry.bind("<Return>", lambda _event: self.refresh_items())

        filters = ttk.Frame(left)
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(3, weight=1)
        ttk.Label(filters, text="Área").grid(row=0, column=0, sticky="w")
        self.area_filter_combo = ttk.Combobox(filters, textvariable=self.area_filter_var, state="readonly", width=18)
        self.area_filter_combo.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Label(filters, text="Tipo").grid(row=0, column=2, sticky="w")
        self.type_filter_combo = ttk.Combobox(filters, textvariable=self.type_filter_var, state="readonly", width=18)
        self.type_filter_combo.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        self.area_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_items())
        self.type_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_items())

        columns = ("id", "title", "area", "type", "source", "updated")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "id": "ID",
            "title": "Título",
            "area": "Área",
            "type": "Tipo",
            "source": "Fuente",
            "updated": "Actualizado",
        }
        widths = {"id": 60, "title": 220, "area": 110, "type": 110, "source": 90, "updated": 140}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.grid(row=3, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_item_selected)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=3, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(left)
        buttons.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Nueva nota", command=self.new_item).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Guardar", command=self.save_item).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Eliminar", command=self.delete_item).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Refrescar", command=self.refresh_items).pack(side="left")

        ttk.Label(right, text="Título").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(right, textvariable=self.title_var).grid(row=0, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Área").grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.area_combo = ttk.Combobox(right, textvariable=self.area_var, state="readonly")
        self.area_combo.grid(row=1, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Tipo").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.type_combo = ttk.Combobox(right, textvariable=self.type_var, state="readonly")
        self.type_combo.grid(row=2, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Etiquetas").grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(right, textvariable=self.tags_var).grid(row=3, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Fuente").grid(row=4, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(right, textvariable=self.source_var).grid(row=4, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Contenido").grid(row=5, column=0, sticky="nw", pady=(0, 4))
        self.content_text = ScrolledText(right, wrap="word", height=18)
        self.content_text.grid(row=5, column=1, sticky="nsew", pady=(0, 8))

        ttk.Label(right, text="Resumen").grid(row=6, column=0, sticky="nw")
        self.summary_text = ScrolledText(right, wrap="word", height=6)
        self.summary_text.grid(row=6, column=1, sticky="nsew")

        ttk.Label(self, textvariable=self.status_var).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

    def _load_reference_values(self) -> None:
        area_rows = self.repo.list_areas()
        type_rows = self.repo.list_item_types()
        self.areas_by_name = {str(row["name"]): int(row["id"]) for row in area_rows}
        self.types_by_name = {str(row["name"]): int(row["id"]) for row in type_rows}
        self.areas_by_name = {"": None, **self.areas_by_name}
        self.types_by_name = {"": None, **self.types_by_name}
        self.area_combo.configure(values=list(self.areas_by_name.keys()))
        self.type_combo.configure(values=list(self.types_by_name.keys()))
        self.area_filter_combo.configure(values=["Todas", *[str(row["name"]) for row in area_rows]])
        self.type_filter_combo.configure(values=["Todos", *[str(row["name"]) for row in type_rows]])
        if not self.area_var.get() and area_rows:
            self.area_var.set(str(area_rows[0]["name"]))
        if not self.type_var.get() and type_rows:
            self.type_var.set(str(type_rows[0]["name"]))

    def _selected_filter_id(self, value: str, mapping: dict[str, int | None], empty_label: str) -> int | None:
        if value == empty_label:
            return None
        return mapping.get(value)

    def refresh_items(self) -> None:
        self._load_reference_values()
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        area_id = self._selected_filter_id(self.area_filter_var.get(), self.areas_by_name, "Todas")
        type_id = self._selected_filter_id(self.type_filter_var.get(), self.types_by_name, "Todos")
        rows = self.repo.list_items(self.search_var.get(), area_id, type_id)
        for row in rows:
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["id"],
                    row["title"] or "",
                    row["area_name"] or "",
                    row["item_type_name"] or "",
                    row["source_type"] or "",
                    row["updated_at"] or row["created_at"] or "",
                ),
            )
        self.status_var.set(f"{len(rows)} notas cargadas")

    def new_item(self) -> None:
        self.current_item_id = None
        self.tree.selection_remove(self.tree.selection())
        self.title_var.set("")
        self.tags_var.set("")
        self.source_var.set("manual")
        self.content_text.delete("1.0", "end")
        self.summary_text.delete("1.0", "end")
        self.title_var.set("")
        self.status_var.set("Nueva nota")

    def _on_item_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        item_id = int(selection[0])
        row = self.repo.get_item(item_id)
        if row is None:
            return
        self.current_item_id = item_id
        self.title_var.set(str(row["title"] or ""))
        self.area_var.set(str(row["area_name"] or ""))
        self.type_var.set(str(row["item_type_name"] or ""))
        self.tags_var.set(", ".join(self.repo.get_tags_for_item(item_id)))
        self.source_var.set(str(row["source_type"] or "manual"))
        self.content_text.delete("1.0", "end")
        self.content_text.insert("1.0", str(row["content"] or ""))
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", str(row["summary"] or ""))
        self.status_var.set(f"Nota seleccionada id={item_id}")

    def _tags_from_entry(self) -> list[str]:
        return [tag.strip() for tag in self.tags_var.get().split(",") if tag.strip()]

    def save_item(self) -> None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Knowledge Manager", "El título es obligatorio.")
            return
        content = self.content_text.get("1.0", "end").strip()
        summary = self.summary_text.get("1.0", "end").strip()
        area_id = self.areas_by_name.get(self.area_var.get())
        type_id = self.types_by_name.get(self.type_var.get())
        tags = self._tags_from_entry()
        try:
            if self.current_item_id is None:
                item_id = self.repo.create_item(
                    title=title,
                    content=content,
                    area_id=area_id,
                    item_type_id=type_id,
                    tags=tags,
                    source_type=self.source_var.get().strip() or "manual",
                    summary=summary,
                )
                self.current_item_id = item_id
                logger.info("KNOWLEDGE: nota creada id=%s", item_id)
                self.status_var.set(f"Nota creada id={item_id}")
            else:
                self.repo.update_item(
                    item_id=self.current_item_id,
                    title=title,
                    content=content,
                    area_id=area_id,
                    item_type_id=type_id,
                    tags=tags,
                    summary=summary,
                )
                logger.info("KNOWLEDGE: nota actualizada id=%s", self.current_item_id)
                self.status_var.set(f"Nota actualizada id={self.current_item_id}")
            self.refresh_items()
            if self.current_item_id is not None:
                self.tree.selection_set(str(self.current_item_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo guardar la nota de conocimiento")
            messagebox.showerror("Knowledge Manager", f"No se pudo guardar la nota.\n\n{exc}")

    def delete_item(self) -> None:
        if self.current_item_id is None:
            messagebox.showwarning("Knowledge Manager", "Selecciona una nota para eliminar.")
            return
        if not messagebox.askyesno("Eliminar", "¿Eliminar la nota seleccionada?"):
            return
        item_id = self.current_item_id
        self.repo.delete_item(item_id)
        logger.info("KNOWLEDGE: nota eliminada id=%s", item_id)
        self.current_item_id = None
        self.new_item()
        self.refresh_items()
        self.status_var.set(f"Nota eliminada id={item_id}")
