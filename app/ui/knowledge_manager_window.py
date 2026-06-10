"""Tkinter window for the Knowledge Manager module."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.persistence.knowledge_repository import KnowledgeRepository
from app.persistence.masters_repository import MastersRepository
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)


class KnowledgeManagerWindow(tk.Toplevel):
    """Basic CRUD interface for manual and sourced knowledge notes."""

    def __init__(self, parent: tk.Misc, db_connection: sqlite3.Connection):
        super().__init__(parent)
        self.repo = KnowledgeRepository(db_connection)
        self.masters_repo = MastersRepository(db_connection)
        self.current_item_id: int | None = None
        self.areas_by_name: dict[str, str] = {}
        self.types_by_name: dict[str, str] = {}
        self.topic_filter_by_name: dict[str, int | None] = {}
        self.topics_by_name: dict[str, int | None] = {}
        self.topic_name_by_id: dict[int, str] = {}

        self.title("Knowledge Manager")
        apply_app_icon(self)
        self.geometry("1180x760")
        self.minsize(980, 620)
        logger.info("KNOWLEDGE: módulo abierto")

        self.search_var = tk.StringVar()
        self.area_filter_var = tk.StringVar(value="Todas")
        self.type_filter_var = tk.StringVar(value="Todos")
        self.topic_filter_var = tk.StringVar(value="Todos")
        self.title_var = tk.StringVar()
        self.area_var = tk.StringVar()
        self.topic_var = tk.StringVar()
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
        right.rowconfigure(6, weight=3)
        right.rowconfigure(7, weight=1)

        ttk.Label(left, text="Buscar").grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(left, textvariable=self.search_var)
        search_entry.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        search_entry.bind("<Return>", lambda _event: self.refresh_items())

        filters = ttk.Frame(left)
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(3, weight=1)
        filters.columnconfigure(5, weight=1)
        ttk.Label(filters, text="Área").grid(row=0, column=0, sticky="w")
        self.area_filter_combo = ttk.Combobox(filters, textvariable=self.area_filter_var, state="readonly", width=16)
        self.area_filter_combo.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Label(filters, text="Tema").grid(row=0, column=2, sticky="w")
        self.topic_filter_combo = ttk.Combobox(filters, textvariable=self.topic_filter_var, state="readonly", width=16)
        self.topic_filter_combo.grid(row=0, column=3, sticky="ew", padx=(4, 8))
        ttk.Label(filters, text="Tipo").grid(row=0, column=4, sticky="w")
        self.type_filter_combo = ttk.Combobox(filters, textvariable=self.type_filter_var, state="readonly", width=16)
        self.type_filter_combo.grid(row=0, column=5, sticky="ew", padx=(4, 0))
        self.area_filter_combo.bind("<<ComboboxSelected>>", self._on_area_filter_changed)
        self.topic_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_items())
        self.type_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_items())

        columns = ("id", "title", "area", "topic", "type", "source", "updated")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "id": "ID",
            "title": "Título",
            "area": "Área",
            "topic": "Tema",
            "type": "Tipo",
            "source": "Fuente",
            "updated": "Actualizado",
        }
        widths = {"id": 60, "title": 220, "area": 110, "topic": 130, "type": 110, "source": 90, "updated": 140}
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
        self.area_combo.bind("<<ComboboxSelected>>", self._on_area_changed)

        ttk.Label(right, text="Tema").grid(row=2, column=0, sticky="w", pady=(0, 4))
        topic_row = ttk.Frame(right)
        topic_row.grid(row=2, column=1, sticky="ew", pady=(0, 4))
        topic_row.columnconfigure(0, weight=1)
        self.topic_combo = ttk.Combobox(topic_row, textvariable=self.topic_var, state="readonly")
        self.topic_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(topic_row, text="Gestionar temas", command=self.open_topics_dialog).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(right, text="Tipo").grid(row=3, column=0, sticky="w", pady=(0, 4))
        self.type_combo = ttk.Combobox(right, textvariable=self.type_var, state="readonly")
        self.type_combo.grid(row=3, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Etiquetas (separadas por coma)").grid(row=4, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(right, textvariable=self.tags_var).grid(row=4, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Fuente").grid(row=5, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(right, textvariable=self.source_var).grid(row=5, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(right, text="Contenido").grid(row=6, column=0, sticky="nw", pady=(0, 4))
        self.content_text = ScrolledText(right, wrap="word", height=18)
        self.content_text.grid(row=6, column=1, sticky="nsew", pady=(0, 8))

        ttk.Label(right, text="Resumen").grid(row=7, column=0, sticky="nw")
        self.summary_text = ScrolledText(right, wrap="word", height=6)
        self.summary_text.grid(row=7, column=1, sticky="nsew")

        ttk.Label(self, textvariable=self.status_var).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

    def _load_reference_values(self) -> None:
        area_values = self.masters_repo.list_active("Area")
        type_values = self.masters_repo.list_active("Tipo")
        self.areas_by_name = {"": "", **{value: value for value in area_values}}
        self.types_by_name = {"": "", **{value: value for value in type_values}}
        self.area_combo.configure(values=list(self.areas_by_name.keys()))
        self.type_combo.configure(values=list(self.types_by_name.keys()))
        self.area_filter_combo.configure(values=["Todas", *area_values])
        self.type_filter_combo.configure(values=["Todos", *type_values])
        if not self.area_var.get() and area_values:
            self.area_var.set(area_values[0])
        if not self.type_var.get() and type_values:
            self.type_var.set(type_values[0])
        self._refresh_topic_filter_values()
        self._refresh_topic_values(keep_value=self.topic_var.get())

    @staticmethod
    def _topic_label(row: sqlite3.Row, include_area: bool) -> str:
        topic_name = str(row["name"] or "")
        area_name = str(row["area_name"] or "")
        if include_area and area_name:
            return f"{area_name} / {topic_name}"
        return topic_name

    def _refresh_topic_filter_values(self) -> None:
        area = self._selected_filter_value(self.area_filter_var.get(), "Todas")
        topic_rows = self.repo.list_topics(area=area)
        include_area = area is None
        self.topic_filter_by_name = {"Todos": None}
        for row in topic_rows:
            self.topic_filter_by_name[self._topic_label(row, include_area)] = int(row["id"])
        values = list(self.topic_filter_by_name.keys())
        self.topic_filter_combo.configure(values=values)
        if self.topic_filter_var.get() not in values:
            self.topic_filter_var.set("Todos")

    def _refresh_topic_values(self, keep_value: str = "", selected_topic_id: int | None = None) -> None:
        area = self.areas_by_name.get(self.area_var.get(), "")
        topic_rows = self.repo.list_topics(area=area) if area else self.repo.list_topics()
        include_area = not area
        self.topics_by_name = {"": None}
        self.topic_name_by_id = {}
        for row in topic_rows:
            label = self._topic_label(row, include_area)
            topic_id = int(row["id"])
            self.topics_by_name[label] = topic_id
            self.topic_name_by_id[topic_id] = label
        values = list(self.topics_by_name.keys())
        self.topic_combo.configure(values=values)
        if selected_topic_id is not None and selected_topic_id in self.topic_name_by_id:
            self.topic_var.set(self.topic_name_by_id[selected_topic_id])
        elif keep_value in values:
            self.topic_var.set(keep_value)
        else:
            self.topic_var.set("")

    def _on_area_filter_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_topic_filter_values()
        self.refresh_items()

    def _on_area_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_topic_values()

    def open_topics_dialog(self) -> None:
        TopicManagerDialog(self, self.repo, self._after_topics_changed)

    def _after_topics_changed(self) -> None:
        self._load_reference_values()
        self.refresh_items()

    def _selected_filter_id(self, value: str, mapping: dict[str, int | None], empty_label: str) -> int | None:
        if value == empty_label:
            return None
        return mapping.get(value)

    @staticmethod
    def _selected_filter_value(value: str, empty_label: str) -> str | None:
        if value == empty_label:
            return None
        return value.strip() or None

    def refresh_items(self) -> None:
        self._load_reference_values()
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        area = self._selected_filter_value(self.area_filter_var.get(), "Todas")
        tipo = self._selected_filter_value(self.type_filter_var.get(), "Todos")
        topic_id = self._selected_filter_id(self.topic_filter_var.get(), self.topic_filter_by_name, "Todos")
        rows = self.repo.list_items(self.search_var.get(), area=area, tipo=tipo, topic_id=topic_id)
        for row in rows:
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["id"],
                    row["title"] or "",
                    row["area_name"] or "",
                    row["topic_name"] or "",
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
        self.topic_var.set("")
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
        topic_id = int(row["topic_id"]) if row["topic_id"] is not None else None
        self._refresh_topic_values(selected_topic_id=topic_id)
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
        area = self.areas_by_name.get(self.area_var.get(), "")
        tipo = self.types_by_name.get(self.type_var.get(), "")
        topic_id = self.topics_by_name.get(self.topic_var.get())
        tags = self._tags_from_entry()
        try:
            if self.current_item_id is None:
                item_id = self.repo.create_item(
                    title=title,
                    content=content,
                    area=area,
                    tipo=tipo,
                    topic_id=topic_id,
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
                    area=area,
                    tipo=tipo,
                    topic_id=topic_id,
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


class TopicManagerDialog(tk.Toplevel):
    """Small dialog to create, edit, and deactivate Knowledge Manager topics."""

    def __init__(self, parent: tk.Misc, repo: KnowledgeRepository, on_change: Callable[[], None]) -> None:
        super().__init__(parent)
        self.repo = repo
        self.masters_repo = MastersRepository(repo.conn)
        self.on_change = on_change
        self.selected_topic_id: int | None = None
        self.areas_by_name: dict[str, str] = {}

        self.title("Gestionar temas")
        self.geometry("720x420")
        self.transient(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.area_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.description_var = tk.StringVar()
        self.active_var = tk.BooleanVar(value=True)

        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        columns = ("id", "area", "name", "active")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        for column, text, width in (
            ("id", "ID", 60),
            ("area", "Área", 140),
            ("name", "Tema", 220),
            ("active", "Activo", 70),
        ):
            self.tree.heading(column, text=text)
            self.tree.column(column, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", self._on_selected)

        form = ttk.Frame(body)
        form.grid(row=0, column=1, sticky="nsew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Área").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.area_combo = ttk.Combobox(form, textvariable=self.area_var, state="readonly")
        self.area_combo.grid(row=0, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(form, text="Nombre").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(form, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(form, text="Descripción").grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(form, textvariable=self.description_var).grid(row=2, column=1, sticky="ew", pady=(0, 4))

        ttk.Checkbutton(form, text="Activo", variable=self.active_var).grid(row=3, column=1, sticky="w", pady=(0, 8))

        buttons = ttk.Frame(form)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="Añadir", command=self.add_topic).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Editar", command=self.edit_topic).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Desactivar", command=self.deactivate_topic).pack(side="left")

    def refresh(self) -> None:
        area_values = self.masters_repo.list_active("Area")
        self.areas_by_name = {"": "", **{value: value for value in area_values}}
        self.area_combo.configure(values=list(self.areas_by_name.keys()))
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        for row in self.repo.list_topics(active_only=False):
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(row["id"], row["area_name"] or "", row["name"] or "", "Sí" if row["active"] else "No"),
            )

    def _on_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        topic_id = int(selection[0])
        rows = [row for row in self.repo.list_topics(active_only=False) if int(row["id"]) == topic_id]
        if not rows:
            return
        row = rows[0]
        self.selected_topic_id = topic_id
        self.area_var.set(str(row["area_name"] or ""))
        self.name_var.set(str(row["name"] or ""))
        self.description_var.set(str(row["description"] or ""))
        self.active_var.set(bool(row["active"]))

    def _area(self) -> str:
        return self.areas_by_name.get(self.area_var.get(), "")

    def add_topic(self) -> None:
        try:
            topic_id = self.repo.create_topic(
                name=self.name_var.get(),
                area=self._area(),
                description=self.description_var.get(),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Gestionar temas", f"No se pudo crear el tema.\n\n{exc}", parent=self)
            return
        self.selected_topic_id = topic_id
        self.active_var.set(True)
        self.refresh()
        self.tree.selection_set(str(topic_id))
        self.on_change()

    def edit_topic(self) -> None:
        if self.selected_topic_id is None:
            messagebox.showwarning("Gestionar temas", "Selecciona un tema para editar.", parent=self)
            return
        try:
            self.repo.update_topic(
                topic_id=self.selected_topic_id,
                name=self.name_var.get(),
                area=self._area(),
                description=self.description_var.get(),
                active=self.active_var.get(),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Gestionar temas", f"No se pudo editar el tema.\n\n{exc}", parent=self)
            return
        self.refresh()
        self.tree.selection_set(str(self.selected_topic_id))
        self.on_change()

    def deactivate_topic(self) -> None:
        if self.selected_topic_id is None:
            messagebox.showwarning("Gestionar temas", "Selecciona un tema para desactivar.", parent=self)
            return
        self.active_var.set(False)
        self.edit_topic()
