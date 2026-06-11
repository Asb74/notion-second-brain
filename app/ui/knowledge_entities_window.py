"""Tkinter browser for offline Knowledge entities."""

from __future__ import annotations

import logging
import sqlite3
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from app.persistence.knowledge_repository import KnowledgeRepository
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)

TYPE_LABELS: list[tuple[str, str]] = [
    ("person", "Personas"),
    ("organization", "Organizaciones"),
    ("email", "Emails"),
    ("phone", "Teléfonos"),
    ("url", "URLs"),
    ("date", "Fechas"),
    ("location", "Lugares"),
    ("other", "Otros"),
]
LABEL_BY_TYPE = dict(TYPE_LABELS)
TYPE_BY_LABEL = {label: entity_type for entity_type, label in TYPE_LABELS}


class KnowledgeEntitiesWindow(tk.Toplevel):
    """Global entity navigation window for Knowledge."""

    def __init__(
        self,
        parent: tk.Misc,
        db_connection: sqlite3.Connection,
        on_open_note: Callable[[int], None] | None = None,
    ):
        super().__init__(parent)
        self.repo = KnowledgeRepository(db_connection)
        self.on_open_note = on_open_note
        self.selected_type = "person"
        self.selected_entity_id: int | None = None
        self._rebuilding = False

        self.title("Entidades de Knowledge")
        apply_app_icon(self)
        self.geometry("1180x680")
        self.minsize(980, 560)

        self.status_var = tk.StringVar(value="Listo")
        self._build_layout()
        self.refresh_all()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        left = ttk.Frame(paned)
        center = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(center, weight=3)
        paned.add(right, weight=4)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ttk.Label(left, text="Tipos de entidad", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.types_tree = ttk.Treeview(left, show="tree", selectmode="browse", height=12)
        self.types_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.types_tree.bind("<<TreeviewSelect>>", self._on_type_selected)

        entity_header = ttk.Frame(center)
        entity_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(entity_header, text="Entidades", font=("TkDefaultFont", 10, "bold")).pack(side="left")

        entity_columns = ("value", "type", "notes", "confidence")
        self.entities_tree = ttk.Treeview(center, columns=entity_columns, show="headings", selectmode="extended")
        headings = {"value": "Valor", "type": "Tipo", "notes": "Nº notas", "confidence": "Confianza media"}
        widths = {"value": 260, "type": 110, "notes": 75, "confidence": 110}
        for column in entity_columns:
            self.entities_tree.heading(column, text=headings[column])
            self.entities_tree.column(column, width=widths[column], anchor="w")
        self.entities_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.entities_tree.bind("<<TreeviewSelect>>", self._on_entity_selected)
        entity_scroll = ttk.Scrollbar(center, orient="vertical", command=self.entities_tree.yview)
        entity_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.entities_tree.configure(yscrollcommand=entity_scroll.set)

        notes_header = ttk.Frame(right)
        notes_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(notes_header, text="Notas relacionadas", font=("TkDefaultFont", 10, "bold")).pack(side="left")

        note_columns = ("title", "area", "topic", "type", "snippet")
        self.notes_tree = ttk.Treeview(right, columns=note_columns, show="headings", selectmode="browse")
        note_headings = {"title": "Título", "area": "Área", "topic": "Tema", "type": "Tipo", "snippet": "Snippet"}
        note_widths = {"title": 180, "area": 90, "topic": 110, "type": 90, "snippet": 320}
        for column in note_columns:
            self.notes_tree.heading(column, text=note_headings[column])
            self.notes_tree.column(column, width=note_widths[column], anchor="w")
        self.notes_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.notes_tree.bind("<Double-1>", lambda _event: self.open_selected_note())
        note_scroll = ttk.Scrollbar(right, orient="vertical", command=self.notes_tree.yview)
        note_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.notes_tree.configure(yscrollcommand=note_scroll.set)

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        ttk.Button(actions, text="Recalcular entidades", command=self.rebuild_entities).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Eliminar entidad seleccionada", command=self.delete_selected_entity).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Fusionar entidades seleccionadas", command=self.merge_selected_entities).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Abrir nota", command=self.open_selected_note).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Refrescar", command=self.refresh_all).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

    def refresh_all(self) -> None:
        self._load_types()
        self.refresh_entities()

    def _load_types(self) -> None:
        counts = {str(row["entity_type"]): int(row["entity_count"] or 0) for row in self.repo.list_entity_types()}
        current = self.selected_type
        for child in self.types_tree.get_children():
            self.types_tree.delete(child)
        for entity_type, label in TYPE_LABELS:
            iid = f"type:{entity_type}"
            self.types_tree.insert("", "end", iid=iid, text=f"{label} ({counts.get(entity_type, 0)})")
        selected_iid = f"type:{current}"
        if self.types_tree.exists(selected_iid):
            self.types_tree.selection_set(selected_iid)
            self.types_tree.focus(selected_iid)

    def refresh_entities(self) -> None:
        for tree in (self.entities_tree, self.notes_tree):
            for child in tree.get_children():
                tree.delete(child)
        rows = self.repo.list_entities(self.selected_type)
        for row in rows:
            entity_id = int(row["id"])
            avg_confidence = float(row["avg_confidence"] or 0.0)
            entity_type = str(row["entity_type"] or "other")
            self.entities_tree.insert(
                "",
                "end",
                iid=f"entity:{entity_id}",
                values=(
                    row["value"] or "",
                    LABEL_BY_TYPE.get(entity_type, entity_type),
                    int(row["note_count"] or 0),
                    f"{avg_confidence:.2f}",
                ),
            )
        self.status_var.set(f"{len(rows)} entidades cargadas")

    def _on_type_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.types_tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        if not iid.startswith("type:"):
            return
        self.selected_type = iid.removeprefix("type:")
        self.selected_entity_id = None
        self.refresh_entities()

    def _on_entity_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.entities_tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        if not iid.startswith("entity:"):
            return
        self.selected_entity_id = int(iid.removeprefix("entity:"))
        self._load_notes_for_entity(self.selected_entity_id)

    def _load_notes_for_entity(self, entity_id: int) -> None:
        for child in self.notes_tree.get_children():
            self.notes_tree.delete(child)
        rows = self.repo.list_notes_for_entity(entity_id)
        for row in rows:
            note_id = int(row["id"])
            self.notes_tree.insert(
                "",
                "end",
                iid=f"note:{note_id}",
                values=(
                    row["title"] or "",
                    row["area_name"] or "",
                    row["topic_name"] or "",
                    row["item_type_name"] or "",
                    row["snippet"] or "",
                ),
            )
        self.status_var.set(f"{len(rows)} notas relacionadas")

    def _selected_entity_ids(self) -> list[int]:
        ids: list[int] = []
        for iid in self.entities_tree.selection():
            value = str(iid)
            if value.startswith("entity:"):
                ids.append(int(value.removeprefix("entity:")))
        return ids

    def _selected_note_id(self) -> int | None:
        selection = self.notes_tree.selection()
        if not selection:
            return None
        iid = str(selection[0])
        if not iid.startswith("note:"):
            return None
        return int(iid.removeprefix("note:"))

    def open_selected_note(self) -> None:
        note_id = self._selected_note_id()
        if note_id is None:
            messagebox.showwarning("Entidades", "Selecciona una nota relacionada.", parent=self)
            return
        if self.on_open_note is not None:
            self.on_open_note(note_id)
        else:
            messagebox.showinfo("Entidades", f"Nota seleccionada: {note_id}", parent=self)

    def delete_selected_entity(self) -> None:
        entity_ids = self._selected_entity_ids()
        if len(entity_ids) != 1:
            messagebox.showwarning("Entidades", "Selecciona una única entidad para eliminar.", parent=self)
            return
        if not messagebox.askyesno("Eliminar entidad", "¿Eliminar la entidad y sus enlaces? Las notas no se eliminarán.", parent=self):
            return
        self.repo.delete_entity(entity_ids[0])
        self.selected_entity_id = None
        self.refresh_all()
        self.status_var.set("Entidad eliminada")

    def merge_selected_entities(self) -> None:
        entity_ids = self._selected_entity_ids()
        if len(entity_ids) < 2:
            messagebox.showwarning("Fusionar entidades", "Selecciona al menos dos entidades del mismo tipo.", parent=self)
            return
        target_id = entity_ids[0]
        if not messagebox.askyesno(
            "Fusionar entidades",
            "Se conservará la primera entidad seleccionada y se moverán a ella los enlaces del resto. ¿Continuar?",
            parent=self,
        ):
            return
        self.repo.merge_entities(target_id, entity_ids[1:])
        self.refresh_all()
        if self.entities_tree.exists(f"entity:{target_id}"):
            self.entities_tree.selection_set(f"entity:{target_id}")
            self.entities_tree.focus(f"entity:{target_id}")
            self._load_notes_for_entity(target_id)
        self.status_var.set("Entidades fusionadas")

    def rebuild_entities(self) -> None:
        if self._rebuilding:
            return
        self._rebuilding = True
        self.configure(cursor="watch")
        self.status_var.set("Recalculando entidades...")
        threading.Thread(target=self._rebuild_worker, daemon=True).start()

    def _rebuild_worker(self) -> None:
        try:
            result = self.repo.rebuild_all_entities()
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_ENTITY: rebuild failed")
            try:
                self.after(0, self._finish_rebuild, None, exc)
            except tk.TclError:
                pass
            return
        try:
            self.after(0, self._finish_rebuild, result, None)
        except tk.TclError:
            logger.info("KNOWLEDGE_ENTITY: ventana cerrada antes de mostrar resultado")

    def _finish_rebuild(self, result: dict[str, int | float] | None, error: Exception | None) -> None:
        self._rebuilding = False
        self.configure(cursor="")
        if error is not None or result is None:
            message = "No se pudieron recalcular las entidades."
            self.status_var.set(message)
            messagebox.showerror("Recalcular entidades", message, parent=self)
            return
        self.refresh_all()
        message = (
            "Recalculo finalizado: "
            f"{int(result.get('notes') or 0)} notas, "
            f"{int(result.get('entities') or 0)} entidades detectadas, "
            f"{int(result.get('links') or 0)} enlaces, "
            f"{int(result.get('errors') or 0)} errores."
        )
        self.status_var.set(message)
        messagebox.showinfo("Recalcular entidades", message, parent=self)
