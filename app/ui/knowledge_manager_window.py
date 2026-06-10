"""Tkinter window for the Knowledge Manager module."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.config.config_paths import knowledge_attachments_dir
from app.persistence.knowledge_repository import KnowledgeRepository
from app.persistence.masters_repository import MastersRepository
from app.ui.app_icons import apply_app_icon
from app.ui.dictation_widgets import attach_dictation
from app.ui.tooltips import add_tooltip

logger = logging.getLogger(__name__)

AUDIO_ATTACHMENT_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}


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
        logger.info("KNOWLEDGE: ayuda contextual cargada")
        self._load_reference_values()
        self.refresh_items()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        help_text = (
            "Área = ámbito principal (Personal, Trabajo, Sansebas, Archivo).  "
            "Tema = materia dentro de un área.  "
            "Tipo = naturaleza del contenido (Nota, Reunión, Documento, Procedimiento...).  "
            "Etiquetas = palabras clave libres separadas por coma.  "
            "Fuente = origen: manual, email, audio, PDF, Evernote..."
        )
        ttk.Label(self, text=help_text, wraplength=1120, foreground="#555555").grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 0)
        )

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=2)
        paned.add(right, weight=3)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(6, weight=1)

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
        add_tooltip(self.area_combo, "Ámbito principal al que pertenece la información.")

        ttk.Label(right, text="Tema").grid(row=2, column=0, sticky="w", pady=(0, 4))
        topic_row = ttk.Frame(right)
        topic_row.grid(row=2, column=1, sticky="ew", pady=(0, 4))
        topic_row.columnconfigure(0, weight=1)
        self.topic_combo = ttk.Combobox(topic_row, textvariable=self.topic_var, state="readonly")
        self.topic_combo.grid(row=0, column=0, sticky="ew")
        add_tooltip(self.topic_combo, "Materia o subgrupo dentro del área seleccionada.")
        ttk.Button(topic_row, text="Gestionar temas", command=self.open_topics_dialog).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(right, text="Tipo").grid(row=3, column=0, sticky="w", pady=(0, 4))
        self.type_combo = ttk.Combobox(right, textvariable=self.type_var, state="readonly")
        self.type_combo.grid(row=3, column=1, sticky="ew", pady=(0, 4))
        add_tooltip(self.type_combo, "Naturaleza del contenido: nota, reunión, documento, procedimiento, etc.")

        ttk.Label(right, text="Etiquetas (separadas por coma)").grid(row=4, column=0, sticky="w", pady=(0, 4))
        tags_entry = ttk.Entry(right, textvariable=self.tags_var)
        tags_entry.grid(row=4, column=1, sticky="ew", pady=(0, 4))
        add_tooltip(tags_entry, "Palabras clave libres separadas por coma para facilitar búsquedas.")

        ttk.Label(right, text="Fuente").grid(row=5, column=0, sticky="w", pady=(0, 4))
        source_entry = ttk.Entry(right, textvariable=self.source_var)
        source_entry.grid(row=5, column=1, sticky="ew", pady=(0, 4))
        add_tooltip(source_entry, "Origen del contenido: manual, email, audio, PDF, Evernote, etc.")

        notebook = ttk.Notebook(right)
        notebook.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

        content_tab = ttk.Frame(notebook, padding=6)
        summary_tab = ttk.Frame(notebook, padding=6)
        attachments_tab = ttk.Frame(notebook, padding=6)
        notebook.add(content_tab, text="Contenido")
        notebook.add(summary_tab, text="Resumen")
        notebook.add(attachments_tab, text="Adjuntos")

        content_tab.columnconfigure(0, weight=1)
        content_tab.rowconfigure(1, weight=1)
        content_header = ttk.Frame(content_tab)
        content_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(content_header, text="Contenido").pack(side="left")
        self.content_text = ScrolledText(content_tab, wrap="word", height=24)
        self.content_text.grid(row=1, column=0, sticky="nsew")
        self.content_dictation_controls = attach_dictation(self.content_text, content_header)
        self.content_dictation_controls.pack(side="right")
        for child in self.content_dictation_controls.winfo_children():
            if isinstance(child, ttk.Button):
                child.configure(text="🎙 Dictar", width=10)
                child.bind(
                    "<Button-1>",
                    lambda _event: logger.info("KNOWLEDGE_CAPTURE: dictado activado item_id=%s", self.current_item_id),
                    add="+",
                )
                add_tooltip(child, "Dicta texto en la posición actual del cursor del contenido.")
                break

        summary_tab.columnconfigure(0, weight=1)
        summary_tab.rowconfigure(1, weight=1)
        summary_header = ttk.Frame(summary_tab)
        summary_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(summary_header, text="Resumen").pack(side="left")
        ttk.Button(summary_header, text="Generar resumen", state="disabled").pack(side="right")
        self.summary_text = ScrolledText(summary_tab, wrap="word", height=24)
        self.summary_text.grid(row=1, column=0, sticky="nsew")

        attachments_tab.columnconfigure(0, weight=1)
        attachments_tab.rowconfigure(0, weight=1)
        attachments_tab.rowconfigure(2, weight=2)

        attachment_columns = ("filename", "type", "size", "date")
        self.attachments_tree = ttk.Treeview(
            attachments_tab, columns=attachment_columns, show="headings", selectmode="browse", height=8
        )
        attachment_headings = {"filename": "Archivo", "type": "Tipo", "size": "Tamaño", "date": "Fecha"}
        attachment_widths = {"filename": 300, "type": 140, "size": 90, "date": 150}
        for column in attachment_columns:
            self.attachments_tree.heading(column, text=attachment_headings[column])
            self.attachments_tree.column(column, width=attachment_widths[column], anchor="w")
        self.attachments_tree.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        self.attachments_tree.bind("<Double-1>", lambda _event: self.open_attachment())
        self.attachments_tree.bind("<<TreeviewSelect>>", self._on_attachment_selected)
        attachments_scrollbar = ttk.Scrollbar(attachments_tab, orient="vertical", command=self.attachments_tree.yview)
        attachments_scrollbar.grid(row=0, column=1, sticky="ns", pady=(0, 6))
        self.attachments_tree.configure(yscrollcommand=attachments_scrollbar.set)

        attachment_buttons = ttk.Frame(attachments_tab)
        attachment_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(attachment_buttons, text="Añadir archivo", command=self.add_attachments).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(attachment_buttons, text="Pegar captura", command=self.paste_clipboard_capture).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(attachment_buttons, text="Abrir", command=self.open_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="Quitar", command=self.remove_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="Abrir carpeta", command=self.open_attachment_folder).pack(side="left")

        preview_frame = ttk.LabelFrame(attachments_tab, text="Vista previa")
        preview_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.attachment_preview_label = ttk.Label(
            preview_frame,
            text="Selecciona un adjunto para ver la vista previa.",
            anchor="center",
            justify="center",
            wraplength=560,
        )
        self.attachment_preview_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.attachment_preview_image = None

        dnd_available = self._setup_drag_and_drop((self, attachments_tab, self.attachments_tree, preview_frame))
        if dnd_available:
            dnd_message = "Arrastra archivos aquí para adjuntarlos."
            add_tooltip(
                self.attachments_tree,
                "Arrastra archivos aquí para adjuntarlos. También acepta audios mp3, wav, m4a y ogg.",
            )
        else:
            dnd_message = "Arrastrar y soltar no está disponible en este entorno. Usa Añadir archivo."
        ttk.Label(attachments_tab, text=dnd_message, foreground="#555555").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        ttk.Label(self, textvariable=self.status_var).grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))

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
        self.refresh_attachments()
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
        self.refresh_attachments()
        self.status_var.set(f"Nota seleccionada id={item_id}")

    def _tags_from_entry(self) -> list[str]:
        return [tag.strip() for tag in self.tags_var.get().split(",") if tag.strip()]

    def save_item(self) -> int | None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Knowledge Manager", "El título es obligatorio.")
            return None
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
                self.refresh_attachments()
            return self.current_item_id
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo guardar la nota de conocimiento")
            messagebox.showerror("Knowledge Manager", f"No se pudo guardar la nota.\n\n{exc}")
            return None

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


    @staticmethod
    def _format_file_size(file_size: int | None) -> str:
        size = int(file_size or 0)
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).name).strip(" ._")
        return cleaned or "archivo"

    def _attachment_item_dir(self, item_id: int) -> Path:
        now = datetime.now()
        return knowledge_attachments_dir() / f"{now:%Y}" / f"{now:%m}" / str(item_id)

    def _unique_attachment_path(self, directory: Path, filename: str) -> Path:
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            candidate = directory / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _selected_attachment_id(self) -> int | None:
        selection = self.attachments_tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def _clear_attachment_preview(self, message: str = "Selecciona un adjunto para ver la vista previa.") -> None:
        if not hasattr(self, "attachment_preview_label"):
            return
        self.attachment_preview_image = None
        self.attachment_preview_label.configure(image="", text=message)

    def _on_attachment_selected(self, _event: tk.Event | None = None) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is None:
            self._clear_attachment_preview()
            return
        self._show_attachment_preview(attachment_id)

    def _show_attachment_preview(self, attachment_id: int) -> None:
        row = self.repo.get_attachment(attachment_id)
        if row is None:
            self._clear_attachment_preview("No se encontró el adjunto seleccionado.")
            self.refresh_attachments()
            return
        path = Path(str(row["stored_path"] or ""))
        mime_type = str(row["mime_type"] or "")
        suffix = path.suffix.lower()
        if not path.exists():
            self._clear_attachment_preview(f"El archivo no existe:\n{path}")
            return
        if suffix in {".png", ".jpg", ".jpeg"} or mime_type in {"image/png", "image/jpeg"}:
            if self._show_image_attachment_preview(path):
                return
        self._show_file_info_preview(row, path)

    def _show_image_attachment_preview(self, path: Path) -> bool:
        if importlib.util.find_spec("PIL") is None or importlib.util.find_spec("PIL.ImageTk") is None:
            return False
        try:
            image_module = importlib.import_module("PIL.Image")
            image_tk_module = importlib.import_module("PIL.ImageTk")
            image = image_module.open(path)
            image.thumbnail((520, 320))
            self.attachment_preview_image = image_tk_module.PhotoImage(image)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo generar la vista previa de imagen de Knowledge")
            return False
        self.attachment_preview_label.configure(image=self.attachment_preview_image, text="")
        return True

    def _show_file_info_preview(self, row: sqlite3.Row, path: Path) -> None:
        file_size = path.stat().st_size if path.exists() else int(row["file_size"] or 0)
        mime_type = str(row["mime_type"] or mimetypes.guess_type(str(path))[0] or "Tipo desconocido")
        file_info = (
            f"Archivo: {row['original_filename'] or row['stored_filename'] or path.name}\n"
            f"Tipo: {mime_type}\n"
            f"Tamaño: {self._format_file_size(file_size)}\n"
            f"Ruta: {path}"
        )
        self.attachment_preview_image = None
        self.attachment_preview_label.configure(image="", text=file_info)

    def _register_attachment_record(
        self,
        item_id: int,
        original_filename: str,
        destination: Path,
        *,
        mime_type: str | None = None,
        source_type: str = "manual",
    ) -> None:
        detected_mime_type = mime_type
        if detected_mime_type is None:
            detected_mime_type, _encoding = mimetypes.guess_type(str(destination))
        file_size = destination.stat().st_size
        self.repo.add_attachment(
            item_id=item_id,
            original_filename=original_filename,
            stored_filename=destination.name,
            stored_path=str(destination),
            mime_type=detected_mime_type or "",
            file_size=file_size,
            source_type=source_type,
        )

    def _add_attachment_paths(self, item_id: int, selected_paths: tuple[str, ...] | list[str]) -> int:
        target_dir = self._attachment_item_dir(item_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        added = 0
        for selected_path in selected_paths:
            source_path = Path(selected_path)
            if not source_path.exists() or not source_path.is_file():
                logger.warning("KNOWLEDGE_ATTACHMENT: archivo inexistente path=%s", source_path)
                continue
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            safe_name = self._safe_filename(source_path.name)
            stored_filename = f"{timestamp}_{safe_name}"
            destination = self._unique_attachment_path(target_dir, stored_filename)
            try:
                shutil.copy2(source_path, destination)
                self._register_attachment_record(item_id, source_path.name, destination, source_type="manual")
            except Exception as exc:  # noqa: BLE001
                logger.exception("No se pudo añadir el adjunto de Knowledge")
                messagebox.showerror(
                    "Añadir adjunto",
                    f"No se pudo añadir el archivo:\n{source_path}\n\n{exc}",
                    parent=self,
                )
                continue
            added += 1
            if source_path.suffix.lower() in AUDIO_ATTACHMENT_EXTENSIONS:
                logger.info("KNOWLEDGE_ATTACHMENT: audio adjuntado item_id=%s file=%s", item_id, destination)
            logger.info("KNOWLEDGE_ATTACHMENT: añadido item_id=%s file=%s", item_id, destination)
        self.refresh_attachments()
        if added:
            self.status_var.set(f"{added} adjunto(s) añadido(s)")
        return added

    def _setup_drag_and_drop(self, widgets: tuple[tk.Misc, ...]) -> bool:
        if importlib.util.find_spec("tkinterdnd2") is not None:
            tkinterdnd2 = importlib.import_module("tkinterdnd2")
            dnd_files = getattr(tkinterdnd2, "DROP_FILES", getattr(tkinterdnd2, "DND_FILES", "DND_Files"))
            registered = 0
            for widget in widgets:
                if not hasattr(widget, "drop_target_register") or not hasattr(widget, "dnd_bind"):
                    continue
                try:
                    widget.drop_target_register(dnd_files)
                    widget.dnd_bind("<<Drop>>", self._handle_files_dropped)
                except Exception:  # noqa: BLE001
                    logger.debug("No se pudo registrar drag&drop para %s", widget, exc_info=True)
                    continue
                registered += 1
            if registered:
                logger.info("KNOWLEDGE_DND: tkinterdnd activo widgets=%s", registered)
                return True

        tkdnd_version = ""
        try:
            tkdnd_version = str(self.tk.call("package", "require", "tkdnd"))
        except Exception:  # noqa: BLE001
            logger.info("KNOWLEDGE_DND: tkinterdnd no disponible")
            return False

        registered = 0
        drop_command = self.register(self._handle_drop_data)
        for widget in widgets:
            try:
                self.tk.call("tkdnd::drop_target", "register", str(widget), "DND_Files")
                self.tk.call("bind", str(widget), "<<Drop:DND_Files>>", f"{drop_command} %D")
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo registrar tkdnd nativo para %s", widget, exc_info=True)
                continue
            registered += 1
        if registered:
            logger.info("KNOWLEDGE_DND: tkdnd activo version=%s widgets=%s", tkdnd_version, registered)
            return True
        logger.info("KNOWLEDGE_DND: tkinterdnd no disponible")
        return False

    def _handle_files_dropped(self, event: tk.Event) -> None:
        self._handle_drop_data(str(getattr(event, "data", "") or ""))

    def _handle_drop_data(self, dropped_data: str) -> None:
        try:
            raw_paths = self.tk.splitlist(dropped_data)
        except Exception:  # noqa: BLE001
            raw_paths = tuple(dropped_data.split())
        logger.info("KNOWLEDGE_DND: drop recibido paths=%s", list(raw_paths))
        file_paths = [path for path in raw_paths if Path(path).is_file()]
        if not file_paths:
            self.status_var.set("No se encontraron archivos para adjuntar")
            return
        item_id = self._ensure_current_item_saved()
        if item_id is None:
            return
        for file_path in file_paths:
            logger.info("KNOWLEDGE_DND: adjuntando archivo=%s", file_path)
        self._add_attachment_paths(item_id, file_paths)

    def _pillow_image_modules(self) -> tuple[object, object] | None:
        if importlib.util.find_spec("PIL") is None or importlib.util.find_spec("PIL.ImageGrab") is None:
            return None
        image_module = importlib.import_module("PIL.Image")
        image_grab_module = importlib.import_module("PIL.ImageGrab")
        return image_module, image_grab_module

    def _save_png_attachment(self, item_id: int, image: object, filename: str, *, log_message: str) -> Path | None:
        target_dir = self._attachment_item_dir(item_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(filename)
        destination = self._unique_attachment_path(target_dir, safe_name)
        try:
            if hasattr(image, "mode") and getattr(image, "mode") not in {"RGB", "RGBA"} and hasattr(image, "convert"):
                image = image.convert("RGBA")
            image.save(destination, format="PNG")
            self._register_attachment_record(
                item_id,
                original_filename=safe_name,
                destination=destination,
                mime_type="image/png",
                source_type="manual",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo guardar la captura en Knowledge")
            messagebox.showerror("Captura", f"No se pudo guardar la captura.\n\n{exc}", parent=self)
            return None
        self.refresh_attachments()
        self.status_var.set(f"Captura guardada: {destination.name}")
        logger.info("%s item_id=%s file=%s", log_message, item_id, destination)
        return destination

    def paste_clipboard_capture(self) -> None:
        item_id = self._ensure_current_item_saved()
        if item_id is None:
            return
        pillow_modules = self._pillow_image_modules()
        if pillow_modules is None:
            messagebox.showwarning(
                "Pegar captura",
                "Pillow no está disponible. Instala Pillow para pegar imágenes desde el portapapeles.",
                parent=self,
            )
            logger.info("KNOWLEDGE_CAPTURE: captura pegada no disponible; Pillow no instalado")
            return
        image_module, image_grab_module = pillow_modules
        try:
            clipboard_content = image_grab_module.grabclipboard()
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo leer imagen del portapapeles")
            messagebox.showwarning("Pegar captura", f"No se pudo leer el portapapeles.\n\n{exc}", parent=self)
            return
        if isinstance(clipboard_content, image_module.Image):
            filename = f"captura_{datetime.now():%Y%m%d_%H%M%S}.png"
            self._save_png_attachment(
                item_id,
                clipboard_content,
                filename,
                log_message="KNOWLEDGE_CAPTURE: captura pegada",
            )
            return
        messagebox.showinfo("Pegar captura", "No hay una imagen en el portapapeles.", parent=self)
        self.status_var.set("No hay imagen en el portapapeles")

    def capture_screen(self) -> None:
        item_id = self._ensure_current_item_saved()
        if item_id is None:
            return
        pillow_modules = self._pillow_image_modules()
        if pillow_modules is None:
            messagebox.showwarning(
                "Capturar pantalla",
                "Próximamente: la captura de pantalla requiere Pillow en esta fase.",
                parent=self,
            )
            logger.info("KNOWLEDGE_CAPTURE: pantalla capturada no disponible; Pillow no instalado")
            return
        _image_module, image_grab_module = pillow_modules
        try:
            screenshot = image_grab_module.grab()
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo capturar la pantalla")
            messagebox.showwarning("Capturar pantalla", f"Próximamente: no se pudo capturar la pantalla.\n\n{exc}", parent=self)
            return
        filename = f"captura_{datetime.now():%Y%m%d_%H%M%S}.png"
        self._save_png_attachment(
            item_id,
            screenshot,
            filename,
            log_message="KNOWLEDGE_CAPTURE: pantalla capturada",
        )

    def refresh_attachments(self) -> None:
        if not hasattr(self, "attachments_tree"):
            return
        for row_id in self.attachments_tree.get_children():
            self.attachments_tree.delete(row_id)
        self._clear_attachment_preview()
        if self.current_item_id is None:
            return
        for row in self.repo.list_attachments(self.current_item_id):
            self.attachments_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["original_filename"] or row["stored_filename"] or "",
                    row["mime_type"] or "",
                    self._format_file_size(row["file_size"]),
                    row["created_at"] or "",
                ),
            )

    def _ensure_current_item_saved(self) -> int | None:
        if self.current_item_id is not None:
            return self.current_item_id
        if not messagebox.askyesno(
            "Añadir adjunto",
            "La nota debe guardarse antes de añadir adjuntos. ¿Quieres guardarla ahora?",
            parent=self,
        ):
            return None
        return self.save_item()

    def add_attachments(self) -> None:
        item_id = self._ensure_current_item_saved()
        if item_id is None:
            return
        selected_paths = filedialog.askopenfilenames(title="Añadir adjuntos a Knowledge", parent=self)
        if not selected_paths:
            return
        self._add_attachment_paths(item_id, list(selected_paths))

    def _open_path_with_default_app(self, path: Path) -> None:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_attachment(self) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is None:
            messagebox.showwarning("Abrir adjunto", "Selecciona un adjunto para abrir.", parent=self)
            return
        row = self.repo.get_attachment(attachment_id)
        if row is None:
            messagebox.showwarning("Abrir adjunto", "No se encontró el adjunto seleccionado.", parent=self)
            self.refresh_attachments()
            return
        path = Path(str(row["stored_path"] or ""))
        if not path.exists():
            messagebox.showerror("Abrir adjunto", f"El archivo no existe:\n{path}", parent=self)
            return
        try:
            self._open_path_with_default_app(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir el adjunto de Knowledge")
            messagebox.showerror("Abrir adjunto", f"No se pudo abrir el archivo.\n\n{exc}", parent=self)
            return
        logger.info("KNOWLEDGE_ATTACHMENT: abierto path=%s", path)

    def remove_attachment(self) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is None:
            messagebox.showwarning("Quitar adjunto", "Selecciona un adjunto para quitar.", parent=self)
            return
        if not messagebox.askyesno(
            "Quitar adjunto",
            "¿Quieres quitar este adjunto de la nota?\n\nEl archivo físico se conservará en la carpeta interna.",
            parent=self,
        ):
            return
        self.repo.delete_attachment(attachment_id)
        logger.info("KNOWLEDGE_ATTACHMENT: eliminado attachment_id=%s", attachment_id)
        self.refresh_attachments()
        self.status_var.set(f"Adjunto quitado id={attachment_id}")

    def open_attachment_folder(self) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is not None:
            row = self.repo.get_attachment(attachment_id)
            if row is None:
                messagebox.showwarning("Abrir carpeta", "No se encontró el adjunto seleccionado.", parent=self)
                self.refresh_attachments()
                return
            folder = Path(str(row["stored_path"] or "")).parent
        elif self.current_item_id is not None:
            folder = self._attachment_item_dir(self.current_item_id)
            folder.mkdir(parents=True, exist_ok=True)
        else:
            messagebox.showwarning("Abrir carpeta", "Selecciona una nota o un adjunto.", parent=self)
            return
        if not folder.exists():
            messagebox.showerror("Abrir carpeta", f"La carpeta no existe:\n{folder}", parent=self)
            return
        try:
            self._open_path_with_default_app(folder)
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir la carpeta de adjuntos de Knowledge")
            messagebox.showerror("Abrir carpeta", f"No se pudo abrir la carpeta.\n\n{exc}", parent=self)
            return
        logger.info("KNOWLEDGE_ATTACHMENT: carpeta abierta path=%s", folder)


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
        self.geometry("760x480")
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
        body.rowconfigure(1, weight=1)

        ttk.Label(
            body,
            text="Los Temas agrupan contenidos dentro de un Área. Ejemplo: Área Trabajo → Tema Liquidaciones.",
            wraplength=720,
            foreground="#555555",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

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
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", self._on_selected)

        form = ttk.Frame(body)
        form.grid(row=1, column=1, sticky="nsew")
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
