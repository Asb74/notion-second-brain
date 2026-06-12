"""Tkinter browser for offline Knowledge entities and their relationships."""

from __future__ import annotations

import logging
import sqlite3
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from app.persistence.knowledge_repository import KnowledgeRepository
from app.services.knowledge_relation_service import KnowledgeRelationService
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
LOW_CONFIDENCE_THRESHOLD = 0.5
LONG_URL_LENGTH = 90
RELATION_LIMIT = 50


class KnowledgeEntitiesWindow(tk.Toplevel):
    """Global entity navigation window for Knowledge."""

    def __init__(
        self,
        parent: tk.Misc,
        db_connection: sqlite3.Connection,
        on_open_note: Callable[[int], None] | None = None,
        initial_entity_id: int | None = None,
    ):
        super().__init__(parent)
        self.repo = KnowledgeRepository(db_connection)
        self.relation_service = KnowledgeRelationService(db_connection)
        self.on_open_note = on_open_note
        self.selected_type = "person"
        self.selected_entity_id: int | None = None
        self._pending_entity_id = initial_entity_id
        self._all_entity_rows: list[sqlite3.Row] = []
        self._rebuilding = False

        self.title("Entidades de Knowledge")
        apply_app_icon(self)
        self.geometry("1320x760")
        self.minsize(1080, 620)

        self.search_var = tk.StringVar()
        self.hide_low_confidence_var = tk.BooleanVar(value=False)
        self.hide_long_urls_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Listo")
        self.profile_text_var = tk.StringVar(value="Selecciona una entidad para ver su perfil.")
        self.relations_hint_var = tk.StringVar(value="")
        self._build_layout()
        self.refresh_all()
        if initial_entity_id is not None:
            self.after(0, lambda: self.select_entity(initial_entity_id))

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
        paned.add(right, weight=5)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        ttk.Label(left, text="Tipos de entidad", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.types_tree = ttk.Treeview(left, show="tree", selectmode="browse", height=12)
        self.types_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.types_tree.bind("<<TreeviewSelect>>", self._on_type_selected)

        filter_box = ttk.LabelFrame(center, text="Filtros")
        filter_box.grid(row=0, column=0, sticky="ew")
        filter_box.columnconfigure(1, weight=1)
        ttk.Label(filter_box, text="Texto").grid(row=0, column=0, sticky="w", padx=(6, 4), pady=4)
        search_entry = ttk.Entry(filter_box, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew", pady=4)
        search_entry.bind("<KeyRelease>", lambda _event: self.refresh_entities())
        ttk.Checkbutton(
            filter_box,
            text=f"Ocultar baja confianza (<{LOW_CONFIDENCE_THRESHOLD:.1f})",
            variable=self.hide_low_confidence_var,
            command=self.refresh_entities,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Checkbutton(
            filter_box,
            text="Ocultar URLs largas",
            variable=self.hide_long_urls_var,
            command=self.refresh_entities,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        entity_header = ttk.Frame(center)
        entity_header.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(entity_header, text="Entidades", font=("TkDefaultFont", 10, "bold")).pack(side="left")

        entity_columns = ("value", "type", "notes", "confidence")
        self.entities_tree = ttk.Treeview(center, columns=entity_columns, show="headings", selectmode="extended")
        headings = {"value": "Valor", "type": "Tipo", "notes": "Nº notas", "confidence": "Confianza media"}
        widths = {"value": 300, "type": 110, "notes": 75, "confidence": 110}
        for column in entity_columns:
            self.entities_tree.heading(column, text=headings[column])
            self.entities_tree.column(column, width=widths[column], anchor="w")
        self.entities_tree.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.entities_tree.bind("<<TreeviewSelect>>", self._on_entity_selected)
        entity_scroll = ttk.Scrollbar(center, orient="vertical", command=self.entities_tree.yview)
        entity_scroll.grid(row=2, column=1, sticky="ns", pady=(6, 0))
        self.entities_tree.configure(yscrollcommand=entity_scroll.set)

        notebook = ttk.Notebook(right)
        notebook.grid(row=0, column=0, sticky="nsew")
        profile_tab = ttk.Frame(notebook, padding=8)
        notes_tab = ttk.Frame(notebook, padding=8)
        related_tab = ttk.Frame(notebook, padding=8)
        timeline_tab = ttk.Frame(notebook, padding=8)
        notebook.add(profile_tab, text="Perfil")
        notebook.add(notes_tab, text="Notas relacionadas")
        notebook.add(related_tab, text="Entidades relacionadas")
        notebook.add(timeline_tab, text="Línea de tiempo")

        profile_tab.columnconfigure(0, weight=1)
        profile_tab.rowconfigure(0, weight=1)
        ttk.Label(profile_tab, textvariable=self.profile_text_var, justify="left", anchor="nw").grid(row=0, column=0, sticky="nsew")

        notes_tab.columnconfigure(0, weight=1)
        notes_tab.rowconfigure(0, weight=1)
        note_columns = ("title", "area", "topic", "type", "date", "snippet")
        self.notes_tree = ttk.Treeview(notes_tab, columns=note_columns, show="headings", selectmode="browse")
        note_headings = {"title": "Título", "area": "Área", "topic": "Tema", "type": "Tipo", "date": "Fecha", "snippet": "Snippet"}
        note_widths = {"title": 210, "area": 90, "topic": 110, "type": 90, "date": 135, "snippet": 300}
        for column in note_columns:
            self.notes_tree.heading(column, text=note_headings[column])
            self.notes_tree.column(column, width=note_widths[column], anchor="w")
        self.notes_tree.grid(row=0, column=0, sticky="nsew")
        self.notes_tree.bind("<Double-1>", lambda _event: self.open_selected_note())
        note_scroll = ttk.Scrollbar(notes_tab, orient="vertical", command=self.notes_tree.yview)
        note_scroll.grid(row=0, column=1, sticky="ns")
        self.notes_tree.configure(yscrollcommand=note_scroll.set)

        related_tab.columnconfigure(0, weight=1)
        related_tab.rowconfigure(1, weight=1)
        ttk.Label(related_tab, textvariable=self.relations_hint_var, foreground="#666666").grid(row=0, column=0, sticky="w", pady=(0, 6))
        related_columns = ("value", "type", "shared", "score")
        self.related_tree = ttk.Treeview(related_tab, columns=related_columns, show="headings", selectmode="browse")
        related_headings = {"value": "Entidad", "type": "Tipo", "shared": "Notas compartidas", "score": "Score"}
        related_widths = {"value": 330, "type": 120, "shared": 130, "score": 80}
        for column in related_columns:
            self.related_tree.heading(column, text=related_headings[column])
            self.related_tree.column(column, width=related_widths[column], anchor="w")
        self.related_tree.grid(row=1, column=0, sticky="nsew")
        self.related_tree.bind("<Double-1>", lambda _event: self.open_related_entity())
        related_scroll = ttk.Scrollbar(related_tab, orient="vertical", command=self.related_tree.yview)
        related_scroll.grid(row=1, column=1, sticky="ns")
        self.related_tree.configure(yscrollcommand=related_scroll.set)

        timeline_tab.columnconfigure(0, weight=1)
        timeline_tab.rowconfigure(0, weight=1)
        timeline_columns = ("date", "title", "area", "topic")
        self.timeline_tree = ttk.Treeview(timeline_tab, columns=timeline_columns, show="headings", selectmode="browse")
        timeline_headings = {"date": "Fecha", "title": "Título", "area": "Área", "topic": "Tema"}
        timeline_widths = {"date": 150, "title": 300, "area": 110, "topic": 140}
        for column in timeline_columns:
            self.timeline_tree.heading(column, text=timeline_headings[column])
            self.timeline_tree.column(column, width=timeline_widths[column], anchor="w")
        self.timeline_tree.grid(row=0, column=0, sticky="nsew")
        self.timeline_tree.bind("<Double-1>", lambda _event: self.open_selected_timeline_note())
        timeline_scroll = ttk.Scrollbar(timeline_tab, orient="vertical", command=self.timeline_tree.yview)
        timeline_scroll.grid(row=0, column=1, sticky="ns")
        self.timeline_tree.configure(yscrollcommand=timeline_scroll.set)

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        ttk.Button(actions, text="Recalcular entidades", command=self.rebuild_entities).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Abrir nota", command=self.open_selected_note).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Buscar notas con esta entidad", command=self.search_notes_with_selected_entity).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Copiar valor", command=self.copy_selected_entity_value).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Eliminar entidad seleccionada", command=self.delete_selected_entity).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Fusionar entidades seleccionadas", command=self.merge_selected_entities).pack(side="left", padx=(0, 6))
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
        for tree in (self.entities_tree, self.notes_tree, self.related_tree, self.timeline_tree):
            for child in tree.get_children():
                tree.delete(child)
        self.profile_text_var.set("Selecciona una entidad para ver su perfil.")
        self.relations_hint_var.set("")
        self._all_entity_rows = self.repo.list_entities(self.selected_type)
        rows = self._filtered_entity_rows(self._all_entity_rows)
        for row in rows:
            self._insert_entity_row(row)
        self.status_var.set(f"{len(rows)} entidades cargadas")

    def _filtered_entity_rows(self, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
        query = self.search_var.get().strip().casefold()
        filtered: list[sqlite3.Row] = []
        for row in rows:
            value = str(row["value"] or "")
            avg_confidence = float(row["avg_confidence"] or 0.0)
            entity_type = str(row["entity_type"] or "other")
            if query and query not in value.casefold():
                continue
            if self.hide_low_confidence_var.get() and avg_confidence < LOW_CONFIDENCE_THRESHOLD:
                continue
            if self.hide_long_urls_var.get() and entity_type == "url" and len(value) > LONG_URL_LENGTH:
                continue
            filtered.append(row)
        return filtered

    def _insert_entity_row(self, row: sqlite3.Row) -> None:
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
        self.load_entity_profile(self.selected_entity_id)

    def select_entity(self, entity_id: int) -> None:
        entity = self.relation_service.get_entity(entity_id)
        if not entity:
            messagebox.showwarning("Entidades", "No se encontró la entidad seleccionada.", parent=self)
            return
        entity_type = str(entity.get("entity_type") or "other")
        self.selected_type = entity_type
        self._load_types()
        self.refresh_entities()
        iid = f"entity:{int(entity_id)}"
        if not self.entities_tree.exists(iid):
            self.search_var.set("")
            self.hide_low_confidence_var.set(False)
            if entity_type == "url":
                self.hide_long_urls_var.set(False)
            self.refresh_entities()
        if self.entities_tree.exists(iid):
            self.entities_tree.selection_set(iid)
            self.entities_tree.focus(iid)
            self.entities_tree.see(iid)
            self.selected_entity_id = int(entity_id)
            self.load_entity_profile(int(entity_id))

    def load_entity_profile(self, entity_id: int) -> None:
        self.configure(cursor="watch")
        self.status_var.set("Cargando relaciones...")
        for tree in (self.notes_tree, self.related_tree, self.timeline_tree):
            for child in tree.get_children():
                tree.delete(child)
        self.profile_text_var.set("Cargando perfil y relaciones...")
        threading.Thread(target=self._profile_worker, args=(int(entity_id),), daemon=True).start()

    def _profile_worker(self, entity_id: int) -> None:
        try:
            profile = self.relation_service.get_entity_profile(entity_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_RELATION: profile failed entity_id=%s", entity_id)
            try:
                self.after(0, self._finish_profile_load, entity_id, None, exc)
            except tk.TclError:
                pass
            return
        try:
            self.after(0, self._finish_profile_load, entity_id, profile, None)
        except tk.TclError:
            logger.info("KNOWLEDGE_RELATION: ventana cerrada antes de mostrar perfil")

    def _finish_profile_load(self, entity_id: int, profile: dict | None, error: Exception | None) -> None:
        self.configure(cursor="")
        if self.selected_entity_id != entity_id:
            return
        if error is not None or profile is None:
            self.profile_text_var.set("No se pudieron cargar las relaciones de la entidad.")
            self.status_var.set("Error cargando relaciones")
            return
        self._render_profile(profile)
        self._load_notes(profile.get("notes", []))
        self._load_related_entities(profile.get("related_entities", []))
        self._load_timeline(profile.get("notes", []))

    def _render_profile(self, profile: dict) -> None:
        entity = profile.get("entity") or {}
        stats = profile.get("stats") or {}
        if not entity:
            self.profile_text_var.set("No se encontró la entidad seleccionada.")
            return
        entity_type = str(entity.get("entity_type") or "other")
        text = (
            f"Valor: {entity.get('value') or ''}\n"
            f"Tipo: {LABEL_BY_TYPE.get(entity_type, entity_type)}\n"
            f"Nº notas: {int(stats.get('notes_count') or 0)}\n"
            f"Confianza media: {float(stats.get('avg_confidence') or 0.0):.2f}\n"
            f"Primera aparición: {stats.get('first_seen_at') or '-'}\n"
            f"Última aparición: {stats.get('last_seen_at') or '-'}\n"
            f"Entidades relacionadas: {int(stats.get('related_entities_count') or 0)}"
        )
        self.profile_text_var.set(text)

    def _load_notes(self, rows: list[dict]) -> None:
        for child in self.notes_tree.get_children():
            self.notes_tree.delete(child)
        for row in rows:
            note_id = int(row.get("id") or 0)
            self.notes_tree.insert(
                "",
                "end",
                iid=f"note:{note_id}",
                values=(
                    row.get("title") or "",
                    row.get("area_name") or "",
                    row.get("topic_name") or "",
                    row.get("item_type_name") or "",
                    row.get("note_date") or "",
                    row.get("snippet") or "",
                ),
            )
        logger.info("KNOWLEDGE_RELATION: notes entity_id=%s count=%s", self.selected_entity_id, len(rows))
        self.status_var.set(f"{len(rows)} notas relacionadas")

    def _load_related_entities(self, rows: list[dict]) -> None:
        for child in self.related_tree.get_children():
            self.related_tree.delete(child)
        if not rows:
            self.relations_hint_var.set("Sin relaciones detectadas.")
            return
        if len(rows) >= RELATION_LIMIT:
            self.relations_hint_var.set(f"Mostrando las {RELATION_LIMIT} relaciones principales.")
        else:
            self.relations_hint_var.set(f"{len(rows)} relaciones detectadas.")
        for row in rows:
            entity_id = int(row.get("entity_id") or 0)
            entity_type = str(row.get("type") or "other")
            self.related_tree.insert(
                "",
                "end",
                iid=f"related:{entity_id}",
                values=(
                    row.get("value") or "",
                    LABEL_BY_TYPE.get(entity_type, entity_type),
                    int(row.get("shared_notes_count") or 0),
                    f"{float(row.get('score') or 0.0):.2f}",
                ),
            )

    def _load_timeline(self, rows: list[dict]) -> None:
        for child in self.timeline_tree.get_children():
            self.timeline_tree.delete(child)
        timeline = sorted(rows, key=lambda item: str(item.get("note_date") or ""), reverse=True)
        for row in timeline:
            note_id = int(row.get("id") or 0)
            self.timeline_tree.insert(
                "",
                "end",
                iid=f"timeline-note:{note_id}",
                values=(row.get("note_date") or "", row.get("title") or "", row.get("area_name") or "", row.get("topic_name") or ""),
            )

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

    def _selected_timeline_note_id(self) -> int | None:
        selection = self.timeline_tree.selection()
        if not selection:
            return None
        iid = str(selection[0])
        if not iid.startswith("timeline-note:"):
            return None
        return int(iid.removeprefix("timeline-note:"))

    def open_selected_note(self) -> None:
        note_id = self._selected_note_id()
        if note_id is None:
            messagebox.showwarning("Entidades", "Selecciona una nota relacionada.", parent=self)
            return
        if self.on_open_note is not None:
            self.on_open_note(note_id)
        else:
            messagebox.showinfo("Entidades", f"Nota seleccionada: {note_id}", parent=self)

    def open_selected_timeline_note(self) -> None:
        note_id = self._selected_timeline_note_id()
        if note_id is None:
            return
        if self.on_open_note is not None:
            self.on_open_note(note_id)

    def open_related_entity(self) -> None:
        selection = self.related_tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        if not iid.startswith("related:"):
            return
        entity_id = int(iid.removeprefix("related:"))
        logger.info("KNOWLEDGE_RELATION: open related entity_id=%s", entity_id)
        self.select_entity(entity_id)

    def search_notes_with_selected_entity(self) -> None:
        if self.selected_entity_id is None:
            messagebox.showwarning("Entidades", "Selecciona una entidad.", parent=self)
            return
        entity = self.relation_service.get_entity(self.selected_entity_id)
        value = str(entity.get("value") or "")
        if value:
            self.search_var.set(value)
        self.status_var.set("Usa la lista de notas relacionadas para navegar por las coincidencias.")

    def copy_selected_entity_value(self) -> None:
        entity_ids = self._selected_entity_ids()
        if len(entity_ids) != 1:
            messagebox.showwarning("Entidades", "Selecciona una única entidad para copiar.", parent=self)
            return
        entity = self.relation_service.get_entity(entity_ids[0])
        value = str(entity.get("value") or "")
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status_var.set("Valor de entidad copiado")

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
        self.select_entity(target_id)
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
