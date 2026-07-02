"""Tkinter window for the Knowledge Manager module."""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    from tkinterdnd2 import DND_FILES
except Exception:  # noqa: BLE001
    DND_FILES = None

from app.config.config_paths import app_data_dir, knowledge_attachments_dir
from app.persistence.knowledge_repository import KnowledgeRepository
from app.persistence.masters_repository import MastersRepository
from app.services.knowledge_indexer_service import get_effective_ocr_origin, get_effective_ocr_text
from app.services.mobile_firebase_publish_service import MobileFirebasePublishError, MobileFirebasePublishService
from app.services.mobile_notes_import_service import MobileNotesImportError, MobileNotesImportService
from app.services.knowledge_summary_service import (
    KnowledgeSummaryConfigError,
    KnowledgeSummaryGenerationError,
    generate_knowledge_summary,
)
from app.ui.app_icons import apply_app_icon
from app.ui.knowledge_entities_window import KnowledgeEntitiesWindow
from app.ui.knowledge_bulk_ocr_dialog import KnowledgeBulkOcrDialog
from app.ui.knowledge_query_dialog import KnowledgeQueryDialog
from app.ui.dictation_widgets import attach_dictation
from app.ui.tooltips import add_tooltip

logger = logging.getLogger(__name__)

AUDIO_ATTACHMENT_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}
EXCEL_ATTACHMENT_EXTENSIONS = {".xls", ".xlsx", ".xlsm", ".xltx", ".ods"}
WORD_ATTACHMENT_EXTENSIONS = {".doc", ".docx", ".odt"}
PDF_ATTACHMENT_EXTENSIONS = {".pdf"}
IMAGE_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
POWERPOINT_ATTACHMENT_EXTENSIONS = {".ppt", ".pptx", ".odp"}
TEXT_ATTACHMENT_EXTENSIONS = {".txt", ".csv", ".log", ".md", ".json", ".xml", ".html", ".htm"}
VIDEO_ATTACHMENT_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
ARCHIVE_ATTACHMENT_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz"}


def open_file_with_default_app(path: Path) -> None:
    """Open a file or folder with the operating system default application."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"El archivo no existe: {path}")

    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


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
        self.after(0, self._maximize_window)
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
        self._summary_generation_in_progress = False
        self._entities_window: KnowledgeEntitiesWindow | None = None

        self._build_layout()
        logger.info("KNOWLEDGE: ayuda contextual cargada")
        self._load_reference_values()
        self.refresh_items()

    def _maximize_window(self) -> None:
        """Open Knowledge Manager with as much screen space as the platform allows."""
        try:
            if sys.platform.startswith("win"):
                self.state("zoomed")
            else:
                self.attributes("-zoomed", True)
            logger.info("KNOWLEDGE_UI: ventana maximizada")
            return
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo maximizar Knowledge Manager con estado nativo", exc_info=True)

        try:
            width = max(self.winfo_screenwidth() - 20, 1180)
            height = max(self.winfo_screenheight() - 80, 760)
            self.geometry(f"{width}x{height}+0+0")
        except Exception:  # noqa: BLE001
            self.geometry("1180x760")
        logger.info("KNOWLEDGE_UI: ventana maximizada")

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

        self.main_paned = ttk.PanedWindow(self, orient="horizontal")
        self.main_paned.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        left = ttk.Frame(self.main_paned)
        right = ttk.Frame(self.main_paned)
        self.main_paned.add(left, weight=1)
        self.main_paned.add(right, weight=3)
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

        columns = ("type", "source", "updated")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings", selectmode="browse")
        headings = {
            "type": "Tipo",
            "source": "Fuente",
            "updated": "Actualizado",
        }
        self.tree.heading("#0", text="Área / Tema / Nota")
        self.tree.column("#0", width=280, minwidth=180, anchor="w", stretch=True)
        widths = {"type": 90, "source": 80, "updated": 130}
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
        ttk.Button(buttons, text="Refrescar", command=self.refresh_items).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Preguntar a Knowledge", command=self.open_query_dialog).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Entidades", command=self.open_entities_window).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Reindexar Knowledge", command=self.reindex_knowledge).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Reindexar Knowledge con OCR", command=self.reindex_knowledge_with_ocr).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="OCR masivo", command=self.open_bulk_ocr_dialog).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Sincronizar datos móviles", command=self.publish_mobile_data).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Importar notas móviles", command=self.import_mobile_notes).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Probar conexión Firebase", command=self.test_mobile_firebase_connection).pack(side="left")

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
        ocr_tab = ttk.Frame(notebook, padding=6)
        entities_tab = ttk.Frame(notebook, padding=6)
        notebook.add(content_tab, text="Contenido")
        notebook.add(summary_tab, text="Resumen")
        notebook.add(attachments_tab, text="Adjuntos")
        notebook.add(ocr_tab, text="OCR")
        notebook.add(entities_tab, text="Entidades")

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
        self.summary_status_label = ttk.Label(summary_header, text="No existe resumen generado.", foreground="#666666")
        self.summary_status_label.pack(side="left", padx=(12, 0))
        self.summary_ai_button = ttk.Button(
            summary_header,
            text="Generar resumen IA",
            command=self.generate_ai_summary,
        )
        self.summary_ai_button.pack(side="right")
        self.summary_text = ScrolledText(summary_tab, wrap="word", height=24)
        self.summary_text.grid(row=1, column=0, sticky="nsew")
        self.summary_text.bind("<KeyRelease>", lambda _event: self._update_summary_controls())

        attachments_tab.columnconfigure(0, weight=1)
        attachments_tab.rowconfigure(1, weight=1)

        attachment_buttons = ttk.Frame(attachments_tab)
        attachment_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(attachment_buttons, text="Añadir archivo", command=self.add_attachments).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(attachment_buttons, text="Pegar captura", command=self.paste_clipboard_capture).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(attachment_buttons, text="Abrir", command=self.open_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="Quitar", command=self.remove_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="Abrir carpeta", command=self.open_attachment_folder).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="OCR / Mejorar", command=self.ocr_selected_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="Ver OCR", command=self.view_selected_attachment_ocr).pack(side="left", padx=(0, 6))
        ttk.Button(attachment_buttons, text="OCR nota", command=self.ocr_current_note_attachments).pack(side="left")

        self.attachments_paned = ttk.PanedWindow(attachments_tab, orient="vertical")
        self.attachments_paned.grid(row=1, column=0, sticky="nsew")

        attachments_list_frame = ttk.Frame(self.attachments_paned)
        preview_frame = ttk.LabelFrame(self.attachments_paned, text="Vista previa")
        self.attachments_paned.add(attachments_list_frame, weight=1)
        self.attachments_paned.add(preview_frame, weight=3)
        attachments_list_frame.columnconfigure(0, weight=1)

        ocr_tab.columnconfigure(0, weight=1)
        ocr_tab.rowconfigure(2, weight=1)
        ocr_buttons = ttk.Frame(ocr_tab)
        ocr_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(ocr_buttons, text="Adjunto:").pack(side="left", padx=(0, 4))
        self.ocr_attachment_var = tk.StringVar()
        self.ocr_attachment_combo = ttk.Combobox(ocr_buttons, textvariable=self.ocr_attachment_var, state="readonly", width=34)
        self.ocr_attachment_combo.pack(side="left", padx=(0, 6))
        self.ocr_attachment_combo.bind("<<ComboboxSelected>>", self._on_ocr_attachment_selected)
        ttk.Button(ocr_buttons, text="Guardar corrección", command=self.save_ocr_tab_correction).pack(side="left", padx=(0, 6))
        ttk.Button(ocr_buttons, text="OCR / Mejorar", command=self.rerun_ocr_tab_attachment).pack(side="left", padx=(0, 6))
        ttk.Button(ocr_buttons, text="Seleccionar zona OCR", command=self.select_ocr_zone_placeholder).pack(side="left", padx=(0, 6))
        ttk.Button(ocr_buttons, text="Restaurar OCR bruto", command=self.restore_ocr_tab_raw).pack(side="left", padx=(0, 6))
        ttk.Button(ocr_buttons, text="Copiar texto", command=self.copy_ocr_tab_text).pack(side="left", padx=(0, 6))
        ttk.Button(ocr_buttons, text="Refrescar OCR", command=self.refresh_ocr_tab).pack(side="left")
        self.ocr_info_var = tk.StringVar(value="Selecciona una nota para ver el OCR reconocido.")
        ttk.Label(ocr_tab, textvariable=self.ocr_info_var).grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.ocr_text = ScrolledText(ocr_tab, wrap="word", height=24)
        self.ocr_text.grid(row=2, column=0, sticky="nsew")

        entities_tab.columnconfigure(0, weight=1)
        entities_tab.rowconfigure(1, weight=1)
        entities_buttons = ttk.Frame(entities_tab)
        entities_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(entities_buttons, text="Recalcular entidades de esta nota", command=self.rebuild_current_note_entities).pack(side="left", padx=(0, 6))
        ttk.Button(entities_buttons, text="Abrir ventana global", command=self.open_entities_window).pack(side="left", padx=(0, 6))
        ttk.Button(entities_buttons, text="Ver relaciones", command=self.open_selected_note_entity_relations).pack(side="left")
        entity_columns = ("value", "type", "source", "confidence")
        self.note_entities_tree = ttk.Treeview(entities_tab, columns=entity_columns, show="headings", selectmode="browse")
        for column, label, width in (
            ("value", "Valor", 260),
            ("type", "Tipo", 120),
            ("source", "Origen", 100),
            ("confidence", "Confianza", 90),
        ):
            self.note_entities_tree.heading(column, text=label)
            self.note_entities_tree.column(column, width=width, anchor="w")
        self.note_entities_tree.grid(row=1, column=0, sticky="nsew")
        self.note_entities_tree.bind("<Double-1>", lambda _event: self.open_selected_note_entity_relations())
        note_entities_scroll = ttk.Scrollbar(entities_tab, orient="vertical", command=self.note_entities_tree.yview)
        note_entities_scroll.grid(row=1, column=1, sticky="ns")
        self.note_entities_tree.configure(yscrollcommand=note_entities_scroll.set)
        attachments_list_frame.columnconfigure(0, weight=1)
        attachments_list_frame.rowconfigure(0, weight=1)

        attachment_columns = ("filename", "type", "size", "ocr", "date")
        self.attachments_tree = ttk.Treeview(
            attachments_list_frame, columns=attachment_columns, show="headings", selectmode="browse", height=6
        )
        attachment_headings = {"filename": "Archivo", "type": "Tipo", "size": "Tamaño", "ocr": "OCR", "date": "Fecha"}
        attachment_widths = {"filename": 280, "type": 130, "size": 90, "ocr": 90, "date": 150}
        for column in attachment_columns:
            self.attachments_tree.heading(column, text=attachment_headings[column])
            self.attachments_tree.column(column, width=attachment_widths[column], anchor="w")
        self.attachments_tree.grid(row=0, column=0, sticky="nsew")
        self.attachments_tree.bind("<Double-1>", lambda _event: self.open_attachment())
        self.attachments_tree.bind("<<TreeviewSelect>>", self._on_attachment_selected)
        attachments_scrollbar = ttk.Scrollbar(attachments_list_frame, orient="vertical", command=self.attachments_tree.yview)
        attachments_scrollbar.grid(row=0, column=1, sticky="ns")
        self.attachments_tree.configure(yscrollcommand=attachments_scrollbar.set)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.attachment_preview_content = ttk.Frame(preview_frame)
        self.attachment_preview_content.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.attachment_preview_content.columnconfigure(0, weight=1)
        self.attachment_preview_content.rowconfigure(0, weight=1)
        self.attachment_preview_label = ttk.Label(
            self.attachment_preview_content,
            text="Selecciona un adjunto para ver la vista previa.",
            anchor="center",
            justify="center",
            wraplength=560,
        )
        self.attachment_preview_label.grid(row=0, column=0, sticky="nsew")
        self.attachment_preview_label.bind("<Button-1>", self._open_preview_attachment)
        self.attachment_preview_content.bind("<Button-1>", self._open_preview_attachment)
        preview_frame.bind("<Configure>", self._schedule_attachment_preview_refresh)
        self.attachment_preview_open_button = ttk.Button(
            preview_frame,
            text="Abrir archivo",
            command=self.open_attachment,
        )
        self._preview_zoom = 1.0
        self._preview_original_pil_image = None
        self._preview_image = None
        self.attachment_preview_image = None
        self._preview_attachment_path: Path | None = None
        self._preview_attachment_id: int | None = None
        self._current_preview_path: Path | None = None
        self._current_preview_type: str | None = None
        self._current_preview_bounds: tuple[int, int] | None = None
        self._preview_after_id: str | None = None
        self._preview_generation_tokens: set[str] = set()

        dnd_available = self._setup_drag_and_drop(
            (
                self.attachments_tree,
                attachments_list_frame,
                attachments_tab,
                preview_frame,
                self.attachment_preview_content,
                self.attachment_preview_label,
            )
        )
        if dnd_available:
            dnd_message = "Arrastra archivos aquí para adjuntarlos."
            add_tooltip(
                self.attachments_tree,
                "Arrastra archivos aquí para adjuntarlos. También acepta audios mp3, wav, m4a y ogg.",
            )
        else:
            dnd_message = "Arrastrar y soltar no está disponible en esta ventana. Usa Añadir archivo."
        ttk.Label(attachments_tab, text=dnd_message, foreground="#555555").grid(row=2, column=0, sticky="ew", pady=(6, 0))

        ttk.Label(self, textvariable=self.status_var).grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.after_idle(self._apply_initial_sash_positions)
        self.after(350, self._apply_initial_sash_positions)

    def _apply_initial_sash_positions(self) -> None:
        self.update_idletasks()
        total_width = self.main_paned.winfo_width()
        if total_width > 0:
            try:
                self.main_paned.sashpos(0, max(int(total_width * 0.25), 240))
                logger.info("KNOWLEDGE_UI: sash inicial 25/75 aplicado")
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo aplicar sash inicial de Knowledge Manager", exc_info=True)
        if hasattr(self, "attachments_paned"):
            total_height = self.attachments_paned.winfo_height()
            if total_height > 0:
                try:
                    self.attachments_paned.sashpos(0, max(int(total_height * 0.50), 160))
                    logger.info("KNOWLEDGE_UI: adjuntos sash 50/50 aplicado")
                except Exception:  # noqa: BLE001
                    logger.debug("No se pudo aplicar sash inicial de adjuntos", exc_info=True)

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


    def publish_mobile_data(self) -> None:
        """Publish local mobile users and Knowledge masters to Firebase Firestore."""
        self.status_var.set("Sincronizando datos móviles...")
        logger.info("MOBILE_FIREBASE_UI: sincronización solicitada desde Knowledge Manager")

        def worker() -> None:
            try:
                summary = MobileFirebasePublishService(self.repo.conn).publish_all()
            except MobileFirebasePublishError as exc:
                error_message = str(exc)
                logger.warning("MOBILE_FIREBASE_UI: sincronización no completada: %s", error_message)
                self.after(0, lambda msg=error_message: self._show_mobile_firebase_publish_error(msg))
            except Exception as exc:  # noqa: BLE001
                error_message = f"Error inesperado sincronizando datos móviles: {exc}"
                logger.exception("MOBILE_FIREBASE_UI: error inesperado sincronizando datos móviles")
                self.after(0, lambda msg=error_message: self._show_mobile_firebase_publish_error(msg))
            else:
                self.after(0, lambda: self._show_mobile_firebase_publish_summary(summary.to_message()))

        threading.Thread(target=worker, daemon=True).start()

    def _show_mobile_firebase_publish_summary(self, message: str) -> None:
        self.status_var.set("Datos móviles sincronizados")
        messagebox.showinfo("Sincronizar datos móviles", message, parent=self)

    def _show_mobile_firebase_publish_error(self, message: str) -> None:
        self.status_var.set("Error sincronizando datos móviles")
        messagebox.showerror("Sincronizar datos móviles", message, parent=self)

    def test_mobile_firebase_connection(self) -> None:
        """Test Firebase Admin SDK credentials and Firestore connectivity."""
        self.status_var.set("Probando conexión Firebase...")
        logger.info("MOBILE_FIREBASE_UI: prueba de conexión solicitada")

        def worker() -> None:
            try:
                message = MobileFirebasePublishService(self.repo.conn).test_connection()
            except MobileFirebasePublishError as exc:
                error_message = str(exc)
                logger.warning("MOBILE_FIREBASE_UI: prueba de conexión no completada: %s", error_message)
                self.after(0, lambda msg=error_message: self._show_mobile_firebase_publish_error(msg))
            except Exception as exc:  # noqa: BLE001
                error_message = f"Error inesperado probando Firebase: {exc}"
                logger.exception("MOBILE_FIREBASE_UI: error inesperado probando Firebase")
                self.after(0, lambda msg=error_message: self._show_mobile_firebase_publish_error(msg))
            else:
                self.after(0, lambda: self._show_mobile_firebase_connection_ok(message))

        threading.Thread(target=worker, daemon=True).start()

    def _show_mobile_firebase_connection_ok(self, message: str) -> None:
        self.status_var.set("Conexión Firebase correcta")
        messagebox.showinfo("Probar conexión Firebase", message, parent=self)


    def import_mobile_notes(self) -> None:
        """Import uploaded mobile notes from Firebase into local Knowledge."""
        self.status_var.set("Importando notas móviles...")
        logger.info("MOBILE_NOTES_IMPORT_UI: importación solicitada desde Knowledge Manager")

        def worker() -> None:
            try:
                summary = MobileNotesImportService(self.repo.conn).import_all_pending_notes()
            except MobileNotesImportError as exc:
                error_message = str(exc)
                logger.warning("MOBILE_NOTES_IMPORT_UI: importación no completada: %s", error_message)
                self.after(0, lambda msg=error_message: self._show_mobile_notes_import_error(msg))
            except Exception as exc:  # noqa: BLE001
                error_message = f"Error inesperado importando notas móviles: {exc}"
                logger.exception("MOBILE_NOTES_IMPORT_UI: error inesperado importando notas móviles")
                self.after(0, lambda msg=error_message: self._show_mobile_notes_import_error(msg))
            else:
                self.after(0, lambda: self._show_mobile_notes_import_summary(summary.to_message()))

        threading.Thread(target=worker, daemon=True).start()

    def _show_mobile_notes_import_summary(self, message: str) -> None:
        self.status_var.set("Notas móviles importadas")
        self.refresh_items()
        messagebox.showinfo("Importar notas móviles", message, parent=self)

    def _show_mobile_notes_import_error(self, message: str) -> None:
        self.status_var.set("Error importando notas móviles")
        messagebox.showerror("Importar notas móviles", message, parent=self)

    def open_entities_window(self, entity_id: int | None = None) -> None:
        if self._entities_window is not None and self._entities_window.winfo_exists():
            self._entities_window.deiconify()
            self._entities_window.lift()
            self._entities_window.focus_force()
            self._entities_window.refresh_all()
            if entity_id is not None:
                self._entities_window.select_entity(int(entity_id))
            return
        self._entities_window = KnowledgeEntitiesWindow(
            self,
            self.repo.conn,
            on_open_note=self.select_note_by_id,
            initial_entity_id=entity_id,
        )

    def _selected_note_entity_id(self) -> int | None:
        if not hasattr(self, "note_entities_tree"):
            return None
        selection = self.note_entities_tree.selection()
        if not selection:
            return None
        iid = str(selection[0])
        if not iid.startswith("entity-link:"):
            return None
        try:
            return int(iid.split(":", 2)[1])
        except (IndexError, ValueError):
            return None

    def open_selected_note_entity_relations(self) -> None:
        entity_id = self._selected_note_entity_id()
        if entity_id is None:
            messagebox.showwarning("Entidades", "Selecciona una entidad de la nota.", parent=self)
            return
        logger.info("KNOWLEDGE_RELATION: open related entity_id=%s", entity_id)
        self.open_entities_window(entity_id=entity_id)

    def refresh_note_entities(self) -> None:
        if not hasattr(self, "note_entities_tree"):
            return
        for child in self.note_entities_tree.get_children():
            self.note_entities_tree.delete(child)
        if self.current_item_id is None:
            return
        try:
            rows = self.repo.list_entities_for_item(self.current_item_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_ENTITY: error note_id=%s reason=%s", self.current_item_id, exc)
            return
        for row in rows:
            confidence = float(row["confidence"] or 0.0)
            self.note_entities_tree.insert(
                "",
                "end",
                iid=f"entity-link:{row['id']}:{row['source']}",
                values=(row["value"] or "", row["entity_type"] or "", row["source"] or "", f"{confidence:.2f}"),
            )

    def rebuild_current_note_entities(self) -> None:
        if self.current_item_id is None:
            messagebox.showwarning("Entidades", "Selecciona una nota guardada para recalcular sus entidades.", parent=self)
            return
        try:
            self.repo.rebuild_entities_for_item(self.current_item_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_ENTITY: error note_id=%s reason=%s", self.current_item_id, exc)
            messagebox.showerror("Entidades", "No se pudieron recalcular las entidades de la nota.", parent=self)
            return
        self.refresh_note_entities()
        if self._entities_window is not None and self._entities_window.winfo_exists():
            self._entities_window.refresh_all()
        self.status_var.set(f"Entidades recalculadas id={self.current_item_id}")

    def open_query_dialog(self) -> None:
        """Open the local Knowledge question dialog from the manager."""
        KnowledgeQueryDialog(self, self.repo.conn, on_open_note=self.select_note_by_id)

    def select_note_by_id(self, note_id: int) -> None:
        """Select and display a note, clearing filters if necessary."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.search_var.set("")
        self.area_filter_var.set("Todas")
        self.topic_filter_var.set("Todos")
        self.type_filter_var.set("Todos")
        self.refresh_items()
        note_iid = f"note:{int(note_id)}"
        if not self.tree.exists(note_iid):
            messagebox.showwarning("Knowledge Manager", "No se encontró la nota seleccionada.", parent=self)
            return
        parent_iid = self.tree.parent(note_iid)
        while parent_iid:
            self.tree.item(parent_iid, open=True)
            parent_iid = self.tree.parent(parent_iid)
        self.tree.selection_set(note_iid)
        self.tree.focus(note_iid)
        self.tree.see(note_iid)
        self._on_item_selected()

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
        rows = sorted(
            rows,
            key=lambda row: (
                str(row["area_name"] or "Sin área").casefold(),
                str(row["topic_name"] or "Sin tema").casefold(),
                str(row["title"] or "").casefold(),
                int(row["id"]),
            ),
        )
        area_nodes: dict[str, str] = {}
        topic_nodes: dict[tuple[str, str], str] = {}
        for row in rows:
            area_name = str(row["area_name"] or "Sin área")
            topic_name = str(row["topic_name"] or "Sin tema")
            area_iid = area_nodes.get(area_name)
            if area_iid is None:
                area_iid = f"area:{area_name}"
                area_nodes[area_name] = area_iid
                self.tree.insert("", "end", iid=area_iid, text=area_name, open=True, values=("", "", ""))
            topic_key = (area_name, topic_name)
            topic_iid = topic_nodes.get(topic_key)
            if topic_iid is None:
                topic_iid = f"topic:{area_name}:{topic_name}"
                topic_nodes[topic_key] = topic_iid
                self.tree.insert(area_iid, "end", iid=topic_iid, text=topic_name, open=True, values=("", "", ""))
            item_type = str(row["item_type_name"] or "Nota")
            self.tree.insert(
                topic_iid,
                "end",
                iid=f"note:{row['id']}",
                text=self._tree_note_text(row["title"]),
                values=(
                    item_type,
                    row["source_type"] or "",
                    row["updated_at"] or row["created_at"] or "",
                ),
            )
        search = self.search_var.get().strip()
        if search and rows:
            snippet = self._search_match_snippet(rows[0], search)
            if snippet:
                self.status_var.set(f"{len(rows)} notas cargadas. Coincidencia: {snippet}")
            else:
                self.status_var.set(f"{len(rows)} notas cargadas")
        else:
            self.status_var.set(f"{len(rows)} notas cargadas")
        logger.info("KNOWLEDGE_TREE: árbol reconstruido items=%s", len(rows))

    @staticmethod
    def _search_match_snippet(row: sqlite3.Row, search: str, context: int = 70) -> str:
        haystack = str(row["indexed_text"] or "") if "indexed_text" in row.keys() else ""
        if not haystack or not search.strip():
            return ""
        try:
            from app.services.knowledge_query_service import extract_phrases, extract_terms, normalize_text

            needles = [*extract_phrases(search), *extract_terms(search)] or [normalize_text(search)]
            normalized_haystack = normalize_text(haystack)
        except Exception:  # noqa: BLE001
            needles = [search.strip().casefold()]
            normalized_haystack = haystack.casefold()
        needle = next((candidate for candidate in needles if candidate and candidate in normalized_haystack), "")
        if not needle:
            return ""
        index = normalized_haystack.find(needle)
        start = max(index - context, 0)
        end = min(index + len(needle) + context, len(haystack))
        prefix = "..." if start else ""
        suffix = "..." if end < len(haystack) else ""
        snippet = " ".join(haystack[start:end].split())
        return f"{prefix}{snippet}{suffix}"

    @staticmethod
    def _tree_note_text(title: object) -> str:
        """Return the display text for a note node without duplicating its type."""
        return str(title or "")

    def new_item(self) -> None:
        self.current_item_id = None
        self.tree.selection_remove(self.tree.selection())
        self.title_var.set("")
        self.tags_var.set("")
        self.topic_var.set("")
        self.source_var.set("manual")
        self.content_text.delete("1.0", "end")
        self.summary_text.delete("1.0", "end")
        self._update_summary_controls()
        self.title_var.set("")
        self.refresh_attachments()
        self.refresh_ocr_tab()
        self.refresh_note_entities()
        self.status_var.set("Nueva nota")

    def _on_item_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        selected_iid = str(selection[0])
        if not selected_iid.startswith("note:"):
            return
        item_id = int(selected_iid.removeprefix("note:"))
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
        self._update_summary_controls()
        self.refresh_attachments()
        self.refresh_ocr_tab()
        self.refresh_note_entities()
        self.status_var.set(f"Nota seleccionada id={item_id}")

    def _current_summary(self) -> str:
        return self.summary_text.get("1.0", "end").strip()

    def _update_summary_controls(self) -> None:
        summary = self._current_summary() if hasattr(self, "summary_text") else ""
        if hasattr(self, "summary_status_label"):
            self.summary_status_label.configure(text="" if summary else "No existe resumen generado.")
        if hasattr(self, "summary_ai_button"):
            text = "Regenerar resumen IA" if summary else "Generar resumen IA"
            state = "disabled" if self._summary_generation_in_progress else "normal"
            self.summary_ai_button.configure(text=text, state=state)

    def generate_ai_summary(self) -> None:
        if self._summary_generation_in_progress:
            return
        if self.current_item_id is None:
            messagebox.showwarning("Resumen IA", "Selecciona una nota guardada para generar el resumen.", parent=self)
            return
        existing_summary = self._current_summary()
        if existing_summary and not messagebox.askyesno(
            "Regenerar resumen IA",
            "La nota ya tiene un resumen. ¿Deseas sobrescribirlo con un nuevo resumen IA?",
            parent=self,
        ):
            return

        note_id = self.current_item_id
        row = self.repo.get_item(note_id)
        if row is None:
            messagebox.showwarning("Resumen IA", "No se encontró la nota seleccionada.", parent=self)
            return
        note = dict(row)
        note["tags"] = self.repo.get_tags_for_item(note_id)
        self._summary_generation_in_progress = True
        self._update_summary_controls()
        self.status_var.set("Generando resumen IA...")
        self.configure(cursor="watch")
        threading.Thread(target=self._generate_ai_summary_worker, args=(note_id, note), daemon=True).start()

    def _generate_ai_summary_worker(self, note_id: int, note: dict[str, object]) -> None:
        try:
            summary = generate_knowledge_summary(note)
        except KnowledgeSummaryConfigError as exc:
            try:
                self.after(0, self._finish_ai_summary_generation, note_id, None, exc)
            except tk.TclError:
                pass
        except KnowledgeSummaryGenerationError as exc:
            try:
                self.after(0, self._finish_ai_summary_generation, note_id, None, exc)
            except tk.TclError:
                pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_SUMMARY: error reason=%s", exc)
            try:
                self.after(0, self._finish_ai_summary_generation, note_id, None, exc)
            except tk.TclError:
                pass
        else:
            try:
                self.after(0, self._finish_ai_summary_generation, note_id, summary, None)
            except tk.TclError:
                logger.info("KNOWLEDGE_SUMMARY: ventana cerrada antes de guardar resultado")

    def _finish_ai_summary_generation(self, note_id: int, summary: str | None, error: Exception | None) -> None:
        self._summary_generation_in_progress = False
        self.configure(cursor="")
        self._update_summary_controls()
        if error is not None or summary is None:
            message = str(error) if error else "No se pudo generar el resumen IA."
            if "No hay configuración IA disponible" in message:
                logger.info("KNOWLEDGE_SUMMARY: skipped no_ai_config")
                messagebox.showwarning("Resumen IA", "No hay configuración IA disponible para generar resumen.", parent=self)
                self.status_var.set("No hay configuración IA disponible para generar resumen.")
            else:
                logger.error("KNOWLEDGE_SUMMARY: error reason=%s", message)
                messagebox.showerror(
                    "Resumen IA",
                    "No se pudo generar el resumen IA. El resumen anterior no se ha modificado.",
                    parent=self,
                )
                self.status_var.set("Error al generar resumen IA")
            return
        try:
            self.repo.update_item_summary(note_id, summary)
            logger.info("KNOWLEDGE_SUMMARY: saved note_id=%s", note_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_SUMMARY: error reason=%s", exc)
            messagebox.showerror(
                "Resumen IA",
                "Se generó el resumen, pero no se pudo guardar en SQLite.",
                parent=self,
            )
            self.status_var.set("Error al guardar resumen IA")
            return

        if self.current_item_id == note_id:
            self.summary_text.delete("1.0", "end")
            self.summary_text.insert("1.0", summary)
        self.refresh_items()
        current_iid = f"note:{note_id}"
        if self.tree.exists(current_iid):
            self.tree.selection_set(current_iid)
            self.tree.see(current_iid)
        self._update_summary_controls()
        self.status_var.set(f"Resumen IA guardado id={note_id}")

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
            self._update_summary_controls()
            self.refresh_items()
            if self.current_item_id is not None:
                current_iid = f"note:{self.current_item_id}"
                if self.tree.exists(current_iid):
                    self.tree.selection_set(current_iid)
                self.refresh_attachments()
                self.refresh_note_entities()
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


    def open_bulk_ocr_dialog(self) -> None:
        """Open the manual controlled bulk OCR dialog for pending Knowledge attachments."""
        KnowledgeBulkOcrDialog(self, self.repo, on_finished=self._after_bulk_ocr_finished)

    def _after_bulk_ocr_finished(self) -> None:
        self.refresh_items()
        self.refresh_attachments()
        self.refresh_ocr_tab()

    def reindex_knowledge(self) -> None:
        self._start_reindex_knowledge(apply_ocr=False)

    def reindex_knowledge_with_ocr(self) -> None:
        if not messagebox.askyesno(
            "Reindexar Knowledge con OCR",
            "El OCR puede tardar. Se aplicará solo a imágenes y PDFs escaneados candidatos. ¿Continuar?",
            parent=self,
        ):
            return
        self._start_reindex_knowledge(apply_ocr=True)

    def _start_reindex_knowledge(self, *, apply_ocr: bool) -> None:
        if getattr(self, "_reindexing", False):
            return
        self._reindexing = True
        self._reindex_apply_ocr = apply_ocr
        self.status_var.set("Reindexando Knowledge con OCR..." if apply_ocr else "Reindexando Knowledge...")
        self.configure(cursor="watch")
        threading.Thread(target=self._reindex_knowledge_worker, args=(apply_ocr,), daemon=True).start()

    def _reindex_knowledge_worker(self, apply_ocr: bool = False) -> None:
        try:
            result = self.repo.reindex_all(apply_ocr=apply_ocr)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_INDEX: reindex failed")
            try:
                self.after(0, self._finish_reindex_knowledge, None, exc)
            except tk.TclError:
                pass
            return
        try:
            self.after(0, self._finish_reindex_knowledge, result, None)
        except tk.TclError:
            logger.info("KNOWLEDGE_INDEX: ventana cerrada antes de mostrar resultado")

    def _finish_reindex_knowledge(self, result: dict[str, int | float] | None, error: Exception | None) -> None:
        self._reindexing = False
        self.configure(cursor="")
        if error is not None or result is None:
            message = f"No se pudo reindexar Knowledge. {error}"
            self.status_var.set(message)
            messagebox.showerror("Reindexar Knowledge", message, parent=self)
            return
        seconds = float(result.get("seconds") or 0.0)
        message = (
            "Reindexación finalizada: "
            f"{int(result.get('ok') or 0)} notas indexadas, "
            f"{int(result.get('errors') or 0)} errores, "
            f"{seconds:.1f}s."
        )
        if getattr(self, "_reindex_apply_ocr", False):
            message += " OCR aplicado a candidatos."
        self.refresh_items()
        self.status_var.set(message)
        messagebox.showinfo("Reindexar Knowledge", message, parent=self)


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

    def _reset_attachment_preview_area(
        self,
        message: str = "",
        *,
        clickable: bool = True,
    ) -> ttk.Label:
        if hasattr(self, "attachment_preview_content"):
            for child in self.attachment_preview_content.winfo_children():
                child.destroy()
            self.attachment_preview_content.columnconfigure(0, weight=1)
            self.attachment_preview_content.rowconfigure(0, weight=1)
            parent = self.attachment_preview_content
        else:
            parent = self
        self._preview_original_pil_image = None
        self._preview_image = None
        self.attachment_preview_image = None
        self.attachment_preview_label = ttk.Label(
            parent,
            text=message,
            anchor="center",
            justify="center",
            wraplength=760,
            cursor="hand2" if clickable else "",
        )
        self.attachment_preview_label.grid(row=0, column=0, sticky="nsew")
        self.attachment_preview_label.bind("<Button-1>", self._open_preview_attachment)
        if hasattr(self, "attachment_preview_open_button"):
            self.attachment_preview_open_button.grid_remove()
        return self.attachment_preview_label

    def _bind_preview_open(self, widget: tk.Misc) -> None:
        widget.bind("<Button-1>", self._open_preview_attachment)
        widget.bind("<Double-1>", self._open_preview_attachment)
        try:
            widget.configure(cursor="hand2")
        except tk.TclError:
            pass

    def _clear_attachment_preview(self, message: str = "Selecciona un adjunto para ver la vista previa.") -> None:
        if not hasattr(self, "attachment_preview_content") and not hasattr(self, "attachment_preview_label"):
            return
        self._preview_attachment_path = None
        self._preview_attachment_id = None
        self._current_preview_path = None
        self._current_preview_type = None
        self._current_preview_bounds = None
        self._preview_zoom = 1.0
        self._reset_attachment_preview_area(message, clickable=False)

    def _on_attachment_selected(self, _event: tk.Event | None = None) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is None:
            self._clear_attachment_preview()
            return
        self._show_attachment_preview(attachment_id)

    def _schedule_attachment_preview_refresh(self, _event: tk.Event | None = None) -> None:
        if not hasattr(self, "_preview_attachment_id") or self._preview_attachment_id is None:
            return
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo cancelar refresco de preview de adjunto", exc_info=True)
        self._preview_after_id = self.after(180, self._refresh_attachment_preview_after_resize)

    def _refresh_attachment_preview_after_resize(self) -> None:
        self._preview_after_id = None
        attachment_id = self._preview_attachment_id
        if attachment_id is None:
            return
        bounds = self._attachment_preview_bounds()
        if self._current_preview_bounds == bounds:
            return
        self._show_attachment_preview(attachment_id, force=True)

    def _open_preview_attachment(self, _event: tk.Event | None = None) -> None:
        path = getattr(self, "_preview_attachment_path", None)
        if path is None:
            return
        if not path.exists():
            messagebox.showerror("Abrir adjunto", f"El archivo no existe:\n{path}", parent=self)
            return
        if not self._open_file_with_default_app(path):
            return
        logger.info("KNOWLEDGE_PREVIEW: click abrir archivo path=%s", path)

    def _activate_preview_click(self, attachment_id: int, path: Path) -> None:
        self._preview_attachment_id = attachment_id
        self._preview_attachment_path = path
        if hasattr(self, "attachment_preview_label"):
            self.attachment_preview_label.configure(cursor="hand2")

    def _show_attachment_preview(self, attachment_id: int, *, force: bool = False) -> None:
        row = self.repo.get_attachment(attachment_id)
        if row is None:
            self._clear_attachment_preview("No se encontró el adjunto seleccionado.")
            self.refresh_attachments()
            return
        path = Path(str(row["stored_path"] or ""))
        mime_type = str(row["mime_type"] or "")
        if not path.exists():
            self._clear_attachment_preview(f"El archivo no existe:\n{path}")
            return
        preview_type = self._attachment_preview_type(path, mime_type)
        is_new_preview_file = self._current_preview_path != path or self._current_preview_type != preview_type
        if is_new_preview_file:
            self._preview_zoom = 1.0
        if not force and not is_new_preview_file:
            logger.info("KNOWLEDGE_PREVIEW: omitido mismo archivo path=%s", path)
            return
        self._activate_preview_click(attachment_id, path)
        self._current_preview_path = path
        self._current_preview_type = preview_type
        self._current_preview_bounds = self._attachment_preview_bounds()
        if preview_type in {"audio", "video", "archive", "file"}:
            type_labels = {"audio": "Audio", "video": "Vídeo", "archive": "Archivo", "file": "Archivo"}
            logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=tipo_no_visual path=%s", path)
            self._show_file_info_preview(row, path, type_label=type_labels[preview_type])
            return

        self._show_cached_visual_attachment_preview(row, path, preview_type)

    @staticmethod
    def _attachment_preview_type(path: Path, mime_type: str) -> str:
        suffix = path.suffix.lower()
        if suffix in IMAGE_ATTACHMENT_EXTENSIONS or mime_type.startswith("image/"):
            return "image"
        if suffix in PDF_ATTACHMENT_EXTENSIONS or mime_type == "application/pdf":
            return "pdf"
        if suffix in EXCEL_ATTACHMENT_EXTENSIONS:
            return "excel"
        if suffix in WORD_ATTACHMENT_EXTENSIONS:
            return "word"
        if suffix in POWERPOINT_ATTACHMENT_EXTENSIONS:
            return "powerpoint"
        if suffix in TEXT_ATTACHMENT_EXTENSIONS or mime_type.startswith("text/"):
            return "text"
        if suffix in AUDIO_ATTACHMENT_EXTENSIONS:
            return "audio"
        if suffix in VIDEO_ATTACHMENT_EXTENSIONS or mime_type.startswith("video/"):
            return "video"
        if suffix in ARCHIVE_ATTACHMENT_EXTENSIONS:
            return "archive"
        return "file"

    @staticmethod
    def _module_available(module_name: str) -> bool:
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def _preview_cache_dir(self) -> Path:
        return app_data_dir() / "knowledge" / "previews"

    def _preview_cache_path(self, path: Path) -> Path:
        stat = path.stat()
        key_source = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        cache_key = hashlib.sha256(key_source.encode("utf-8", errors="ignore")).hexdigest()
        return self._preview_cache_dir() / f"{cache_key}.png"

    def _show_cached_visual_attachment_preview(self, row: sqlite3.Row, path: Path, preview_type: str) -> None:
        cache_path = self._preview_cache_path(path)
        if cache_path.exists():
            if preview_type == "excel":
                logger.info("KNOWLEDGE_PREVIEW: excel visual cache hit path=%s cache=%s", path, cache_path)
            logger.info("KNOWLEDGE_PREVIEW: preview cache hit path=%s cache=%s", path, cache_path)
            if self._display_cached_preview_png(cache_path):
                return
            if preview_type == "excel":
                logger.info("KNOWLEDGE_PREVIEW: excel visual fallback reason=cache_no_mostrable path=%s", path)
            logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=cache_no_mostrable path=%s", path)
            self._show_file_info_preview(row, path, type_label=self._preview_type_label(preview_type))
            return

        if preview_type == "excel":
            logger.info("KNOWLEDGE_PREVIEW: excel visual generating path=%s cache=%s", path, cache_path)
        logger.info("KNOWLEDGE_PREVIEW: preview cache miss path=%s cache=%s", path, cache_path)
        token = str(cache_path)
        self._reset_attachment_preview_area("Generando vista previa...\n\nHaz clic para abrir el archivo.")
        self._bind_preview_open(self.attachment_preview_label)
        if token in self._preview_generation_tokens:
            return
        self._preview_generation_tokens.add(token)
        logger.info("KNOWLEDGE_PREVIEW: preview async started path=%s", path)
        thread = threading.Thread(
            target=self._generate_preview_png_async,
            args=(row, path, preview_type, cache_path, token),
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _preview_type_label(preview_type: str) -> str:
        return {
            "image": "Imagen",
            "pdf": "PDF",
            "excel": "Excel",
            "word": "Word",
            "powerpoint": "PowerPoint",
            "text": "Documento",
        }.get(preview_type, "Archivo")

    def _generate_preview_png_async(
        self,
        row: sqlite3.Row,
        path: Path,
        preview_type: str,
        cache_path: Path,
        token: str,
    ) -> None:
        success = False
        reason = "desconocido"
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            success, reason = self._generate_preview_png(path, preview_type, cache_path)
        except Exception as exc:  # noqa: BLE001
            reason = f"error:{exc}"
            logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=%s path=%s", reason, path)
        finally:
            self._preview_generation_tokens.discard(token)
        logger.info("KNOWLEDGE_PREVIEW: preview async finished success=%s reason=%s path=%s", success, reason, path)
        try:
            self.after(0, self._finish_preview_generation, row, path, preview_type, cache_path, success, reason)
        except tk.TclError:
            logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=ventana_cerrada path=%s", path)

    def _finish_preview_generation(
        self,
        row: sqlite3.Row,
        path: Path,
        preview_type: str,
        cache_path: Path,
        success: bool,
        reason: str,
    ) -> None:
        if self._preview_attachment_path != path or self._current_preview_type != preview_type:
            return
        if success and cache_path.exists() and self._display_cached_preview_png(cache_path):
            if preview_type == "excel":
                logger.info("KNOWLEDGE_PREVIEW: excel visual ok path=%s cache=%s", path, cache_path)
            return
        if preview_type == "excel":
            logger.info("KNOWLEDGE_PREVIEW: excel visual fallback reason=%s path=%s", reason, path)
        logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=%s path=%s", reason, path)
        message = (
            "Vista previa Excel no disponible. Haz clic para abrir el archivo."
            if preview_type == "excel"
            else "Vista previa visual no disponible. Haz clic para abrir el archivo."
        )
        self._show_file_info_preview(row, path, type_label=self._preview_type_label(preview_type), extra_message=message)

    def _display_cached_preview_png(self, cache_path: Path) -> bool:
        if not self._module_available("PIL") or not self._module_available("PIL.ImageTk"):
            logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=pillow_no_disponible cache=%s", cache_path)
            return False
        image_module = importlib.import_module("PIL.Image")
        image_tk_module = importlib.import_module("PIL.ImageTk")
        try:
            with image_module.open(cache_path) as image:
                self._display_pil_preview(image, image_tk_module)
            logger.info("KNOWLEDGE_PREVIEW: preview cache mostrado cache=%s", cache_path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.info("KNOWLEDGE_PREVIEW: preview fallback reason=png_error:%s cache=%s", exc, cache_path)
            return False

    def _generate_preview_png(self, path: Path, preview_type: str, cache_path: Path) -> tuple[bool, str]:
        if preview_type == "image":
            return self._generate_image_thumbnail(path, cache_path)
        if preview_type == "pdf":
            return self._render_pdf_first_page_to_png(path, cache_path)
        if preview_type in {"word", "excel", "powerpoint"}:
            return self._generate_office_preview(path, preview_type, cache_path)
        if preview_type == "text":
            return self._generate_text_preview(path, cache_path)
        return False, f"tipo_no_soportado:{preview_type}"

    def _generate_image_thumbnail(self, path: Path, cache_path: Path) -> tuple[bool, str]:
        if not self._module_available("PIL"):
            return False, "pillow_no_disponible"
        image_module = importlib.import_module("PIL.Image")
        try:
            with image_module.open(path) as image:
                image.thumbnail((1600, 1600))
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGB")
                image.save(cache_path, "PNG")
            return True, "image_thumbnail"
        except Exception as exc:  # noqa: BLE001
            return False, f"image_error:{exc}"

    def _render_pdf_first_page_to_png(self, pdf_path: Path, cache_path: Path) -> tuple[bool, str]:
        if not self._module_available("fitz"):
            return False, "pymupdf_no_disponible"
        fitz = importlib.import_module("fitz")

        document = None
        try:
            document = fitz.open(str(pdf_path))
            if document.page_count < 1:
                return False, "pdf_sin_paginas"
            page = document.load_page(0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pixmap.save(str(cache_path))
            return True, "pymupdf"
        except Exception as exc:  # noqa: BLE001
            return False, f"pymupdf_error:{exc}"
        finally:
            if document is not None:
                document.close()

    def _generate_office_preview(self, path: Path, preview_type: str, cache_path: Path) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="knowledge_preview_") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pdf_path, reason = self._convert_document_to_pdf(path, temp_dir, allow_word_com=preview_type == "word")
            if pdf_path is not None:
                rendered, render_reason = self._render_pdf_first_page_to_png(pdf_path, cache_path)
                return (True, f"{reason}+{render_reason}") if rendered else (False, render_reason)
        if preview_type == "excel":
            if sys.platform.startswith("win"):
                with tempfile.TemporaryDirectory(prefix="knowledge_preview_excel_com_") as temp_dir_name:
                    temp_dir = Path(temp_dir_name)
                    pdf_path, excel_reason = self._convert_excel_to_pdf_with_com(path, temp_dir)
                    if pdf_path is not None:
                        rendered, render_reason = self._render_pdf_first_page_to_png(pdf_path, cache_path)
                        return (True, f"{excel_reason}+{render_reason}") if rendered else (False, render_reason)
                    reason = f"{reason};{excel_reason}"
            return False, reason
        return False, reason

    def _convert_document_to_pdf(
        self,
        path: Path,
        temp_dir: Path,
        *,
        allow_word_com: bool = False,
    ) -> tuple[Path | None, str]:
        pdf_path, reason = self._convert_word_to_pdf_with_libreoffice(path, temp_dir)
        if pdf_path is not None or not allow_word_com or not sys.platform.startswith("win"):
            return pdf_path, reason
        return self._convert_word_to_pdf_with_com(path, temp_dir)

    def _generate_text_preview(self, path: Path, cache_path: Path) -> tuple[bool, str]:
        return self._generate_tabular_text_image(path, cache_path)

    def _generate_tabular_text_image(self, path: Path, cache_path: Path) -> tuple[bool, str]:
        if not self._module_available("PIL"):
            return False, "pillow_no_disponible"
        image_module = importlib.import_module("PIL.Image")
        image_draw_module = importlib.import_module("PIL.ImageDraw")
        image_font_module = importlib.import_module("PIL.ImageFont")
        try:
            title = path.name
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = [line.rstrip("\n") for _, line in zip(range(32), handle)]

            width, height = 1200, 900
            image = image_module.new("RGB", (width, height), "white")
            draw = image_draw_module.Draw(image)
            font = image_font_module.load_default()
            draw.rectangle((0, 0, width, 64), fill="#f0f3f8")
            draw.text((24, 22), title[:140], fill="#111827", font=font)
            y = 92
            for line in lines:
                if y > height - 36:
                    break
                draw.text((24, y), line[:180], fill="#1f2937", font=font)
                y += 26
            image.save(cache_path, "PNG")
            return True, "text_visual"
        except Exception as exc:  # noqa: BLE001
            return False, f"visual_text_error:{exc}"

    def _convert_word_to_pdf_with_libreoffice(self, path: Path, temp_dir: Path) -> tuple[Path | None, str]:
        soffice_path = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice_path is None:
            return None, "libreoffice_no_encontrado"

        command = [
            soffice_path,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(temp_dir),
            str(path),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            return None, "libreoffice_timeout"
        except OSError as exc:
            return None, f"libreoffice_error:{exc}"

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "sin_detalle").strip().splitlines()[:1]
            return None, f"libreoffice_rc_{completed.returncode}:{detail[0] if detail else 'sin_detalle'}"

        expected_pdf = temp_dir / f"{path.stem}.pdf"
        if expected_pdf.exists():
            return expected_pdf, "libreoffice"
        pdf_candidates = sorted(temp_dir.glob("*.pdf"))
        if pdf_candidates:
            return pdf_candidates[0], "libreoffice"
        return None, "libreoffice_sin_pdf"

    def _convert_word_to_pdf_with_com(self, path: Path, temp_dir: Path) -> tuple[Path | None, str]:
        if not self._module_available("win32com.client"):
            return None, "word_com_no_disponible"
        win32com_client = importlib.import_module("win32com.client")

        pdf_path = temp_dir / f"{path.stem}.pdf"
        word_app = None
        document = None
        try:
            word_app = win32com_client.DispatchEx("Word.Application")
            word_app.Visible = False
            document = word_app.Documents.Open(str(path), ReadOnly=True, AddToRecentFiles=False)
            document.ExportAsFixedFormat(str(pdf_path), 17)
        except Exception as exc:  # noqa: BLE001
            return None, f"word_com_error:{exc}"
        finally:
            if document is not None:
                try:
                    document.Close(False)
                except Exception:  # noqa: BLE001
                    logger.debug("No se pudo cerrar documento Word COM", exc_info=True)
            if word_app is not None:
                try:
                    word_app.Quit()
                except Exception:  # noqa: BLE001
                    logger.debug("No se pudo cerrar Word COM", exc_info=True)

        if pdf_path.exists():
            return pdf_path, "word_com"
        return None, "word_com_sin_pdf"

    def _convert_excel_to_pdf_with_com(self, path: Path, temp_dir: Path) -> tuple[Path | None, str]:
        if not self._module_available("win32com.client"):
            return None, "excel_com_no_disponible"
        win32com_client = importlib.import_module("win32com.client")

        pdf_path = temp_dir / f"{path.stem}.pdf"
        excel_app = None
        workbook = None
        try:
            excel_app = win32com_client.DispatchEx("Excel.Application")
            excel_app.Visible = False
            excel_app.DisplayAlerts = False
            workbook = excel_app.Workbooks.Open(str(path), ReadOnly=True)
            worksheet = workbook.Worksheets(1)
            worksheet.ExportAsFixedFormat(0, str(pdf_path))
        except Exception as exc:  # noqa: BLE001
            return None, f"excel_com_error:{exc}"
        finally:
            if workbook is not None:
                try:
                    workbook.Close(False)
                except Exception:  # noqa: BLE001
                    logger.debug("No se pudo cerrar workbook Excel COM", exc_info=True)
            if excel_app is not None:
                try:
                    excel_app.Quit()
                except Exception:  # noqa: BLE001
                    logger.debug("No se pudo cerrar Excel COM", exc_info=True)

        if pdf_path.exists():
            return pdf_path, "excel_com"
        return None, "excel_com_sin_pdf"

    def _display_pil_preview(self, image: object, image_tk_module: object) -> None:
        self._reset_attachment_preview_area()
        width, height = self._attachment_preview_bounds()
        self._current_preview_bounds = (width, height)
        if hasattr(image, "copy"):
            image = image.copy()
        self._preview_original_pil_image = image
        self._render_preview_image_from_original(image_tk_module)

    def _render_preview_image_from_original(self, image_tk_module: object | None = None) -> None:
        original = self._preview_original_pil_image
        if original is None:
            return
        if image_tk_module is None:
            if not self._module_available("PIL.ImageTk"):
                return
            image_tk_module = importlib.import_module("PIL.ImageTk")

        width, height = self._attachment_preview_bounds()
        original_width = max(int(getattr(original, "width", 1)), 1)
        original_height = max(int(getattr(original, "height", 1)), 1)
        fit_ratio = min(width / original_width, height / original_height, 1.0)
        scale = max(0.3, min(3.0, self._preview_zoom)) * fit_ratio
        target_size = (max(int(original_width * scale), 1), max(int(original_height * scale), 1))

        image = original.copy() if hasattr(original, "copy") else original
        if hasattr(image, "resize"):
            resampling_module = importlib.import_module("PIL.Image")
            resampling = getattr(getattr(resampling_module, "Resampling", resampling_module), "LANCZOS", 1)
            image = image.resize(target_size, resampling)
        self._preview_image = image_tk_module.PhotoImage(image)
        self.attachment_preview_image = self._preview_image
        self._show_preview_image_on_canvas(target_size)

    def _show_preview_image_on_canvas(self, image_size: tuple[int, int]) -> None:
        for child in self.attachment_preview_content.winfo_children():
            child.destroy()
        self.attachment_preview_content.columnconfigure(0, weight=1)
        self.attachment_preview_content.rowconfigure(0, weight=1)

        canvas = tk.Canvas(self.attachment_preview_content, highlightthickness=0, cursor="hand2")
        vertical_scrollbar = ttk.Scrollbar(self.attachment_preview_content, orient="vertical", command=canvas.yview)
        horizontal_scrollbar = ttk.Scrollbar(self.attachment_preview_content, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vertical_scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        bounds_width, bounds_height = self._attachment_preview_bounds()
        x = max((bounds_width - image_size[0]) // 2, 0)
        y = max((bounds_height - image_size[1]) // 2, 0)
        canvas.create_image(x, y, anchor="nw", image=self._preview_image)
        canvas.configure(scrollregion=(0, 0, max(image_size[0], bounds_width), max(image_size[1], bounds_height)))
        self.attachment_preview_label = canvas
        self._bind_preview_open(canvas)
        self._bind_preview_zoom(canvas)
        canvas.focus_set()

    def _bind_preview_zoom(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_preview_mousewheel)
        widget.bind("<Button-4>", self._on_preview_mousewheel)
        widget.bind("<Button-5>", self._on_preview_mousewheel)

    def _on_preview_mousewheel(self, event: tk.Event) -> str:
        if self._preview_original_pil_image is None:
            return "break"
        delta = getattr(event, "delta", 0)
        button_number = getattr(event, "num", None)
        zoom_factor = 1.1 if delta > 0 or button_number == 4 else 0.9
        next_zoom = max(0.3, min(3.0, self._preview_zoom * zoom_factor))
        if next_zoom == self._preview_zoom:
            return "break"
        self._preview_zoom = next_zoom
        self._render_preview_image_from_original()
        return "break"

    def _attachment_preview_bounds(self) -> tuple[int, int]:
        self.update_idletasks()
        current_width = self.attachment_preview_content.winfo_width()
        current_height = self.attachment_preview_content.winfo_height()
        width = current_width - 24 if current_width > 120 else 720
        height = current_height - 24 if current_height > 120 else 500
        return width, height

    def _show_file_info_preview(
        self,
        row: sqlite3.Row,
        path: Path,
        *,
        type_label: str = "Archivo",
        extra_message: str | None = None,
    ) -> None:
        file_size = path.stat().st_size if path.exists() else int(row["file_size"] or 0)
        mime_type = str(row["mime_type"] or mimetypes.guess_type(str(path))[0] or "Tipo desconocido")
        filename = str(row["original_filename"] or row["stored_filename"] or path.name)
        icon_by_type = {
            "PDF": "📄",
            "Excel": "📊",
            "Word": "📝",
            "Audio": "🎧",
            "Imagen": "🖼️",
            "Archivo": "📎",
        }
        icon = icon_by_type.get(type_label, "📎")
        preview_message = extra_message or "Vista previa no disponible. Haz clic para abrir el archivo."
        card_text = (
            f"{icon}  {type_label}\n\n"
            f"{filename}\n\n"
            f"Tipo: {mime_type}\n"
            f"Tamaño: {self._format_file_size(file_size)}\n"
            f"Ruta: {path}\n\n"
            f"{preview_message}"
        )
        label = self._reset_attachment_preview_area(card_text)
        self._bind_preview_open(label)
        logger.info("KNOWLEDGE_PREVIEW: tarjeta fallback tipo=%s path=%s", type_label, path)

    def _register_attachment_record(
        self,
        item_id: int,
        original_filename: str,
        destination: Path,
        *,
        mime_type: str | None = None,
        source_type: str = "manual",
    ) -> int:
        detected_mime_type = mime_type
        if detected_mime_type is None:
            detected_mime_type, _encoding = mimetypes.guess_type(str(destination))
        file_size = destination.stat().st_size
        return self.repo.add_attachment(
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
        first_added_attachment_id: int | None = None
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
                attachment_id = self._register_attachment_record(
                    item_id, source_path.name, destination, source_type="manual"
                )
                if first_added_attachment_id is None:
                    first_added_attachment_id = attachment_id
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
            if first_added_attachment_id is not None:
                self._select_attachment(first_added_attachment_id)
        return added

    def _setup_drag_and_drop(self, widgets: tuple[tk.Misc, ...]) -> bool:
        if DND_FILES is None:
            logger.info("KNOWLEDGE_DND: no habilitado reason=DND_FILES no disponible")
            return False

        registered = 0
        for widget in widgets:
            if not hasattr(widget, "drop_target_register"):
                logger.info(
                    "KNOWLEDGE_DND: registro fallido reason=widget sin drop_target_register widget=%s",
                    widget,
                )
                continue
            if not hasattr(widget, "dnd_bind"):
                logger.info("KNOWLEDGE_DND: registro fallido reason=widget sin dnd_bind widget=%s", widget)
                continue
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._handle_files_dropped)
            except Exception as exc:  # noqa: BLE001
                logger.info("KNOWLEDGE_DND: registro fallido reason=%s widget=%s", exc, widget)
                logger.debug("Detalle de fallo registrando drag&drop", exc_info=True)
                continue
            registered += 1

        if registered:
            logger.info("KNOWLEDGE_DND: registrado correctamente widgets=%s", registered)
            return True

        logger.info("KNOWLEDGE_DND: no habilitado reason=ningún widget aceptó registro")
        return False

    def _handle_files_dropped(self, event: tk.Event) -> None:
        self._handle_drop_data(str(getattr(event, "data", "") or ""))

    def _handle_drop_data(self, dropped_data: str) -> None:
        try:
            raw_paths = self.tk.splitlist(dropped_data)
        except Exception as exc:  # noqa: BLE001
            logger.info("KNOWLEDGE_DND: splitlist falló reason=%s", exc)
            raw_paths = tuple(dropped_data.split())
        file_paths = [path for path in raw_paths if Path(path).is_file()]
        logger.info("KNOWLEDGE_DND: archivos recibidos=%s", len(file_paths))
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

    def _select_attachment(self, attachment_id: int) -> None:
        if not hasattr(self, "attachments_tree"):
            return
        item_id = str(attachment_id)
        if not self.attachments_tree.exists(item_id):
            return
        self.attachments_tree.selection_set(item_id)
        self.attachments_tree.focus(item_id)
        self.attachments_tree.see(item_id)
        self._show_attachment_preview(attachment_id)

    def refresh_attachments(self) -> None:
        if not hasattr(self, "attachments_tree"):
            return
        for row_id in self.attachments_tree.get_children():
            self.attachments_tree.delete(row_id)
        self._clear_attachment_preview()
        if self.current_item_id is None:
            return
        attachment_rows = self.repo.list_attachments(self.current_item_id)
        logger.info("KNOWLEDGE_ATTACHMENTS: load note_id=%s count=%s", self.current_item_id, len(attachment_rows))
        for row in attachment_rows:
            try:
                ocr_status = self._format_ocr_status(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning("KNOWLEDGE_ATTACHMENTS: ocr_status format error reason=%s", exc)
                ocr_status = ""
            self.attachments_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["original_filename"] or row["stored_filename"] or "",
                    row["mime_type"] or "",
                    self._format_file_size(row["file_size"]),
                    ocr_status,
                    row["created_at"] or "",
                ),
            )

    def _attachment_ocr_texts(self, row: sqlite3.Row) -> tuple[str, str, str]:
        raw = str(row["ocr_text_raw"] or row["ocr_text"] or "") if "ocr_text_raw" in row.keys() else str(row["ocr_text"] or "")
        corrected = str(row["ocr_text_corrected"] or "") if "ocr_text_corrected" in row.keys() else ""
        return raw, corrected, get_effective_ocr_text(row)

    def _ocr_selected_attachment_id(self) -> int | None:
        if not hasattr(self, "ocr_attachment_combo"):
            return None
        value = self.ocr_attachment_combo.get()
        if not value:
            return None
        try:
            return int(value.split(" | ", 1)[0])
        except (TypeError, ValueError):
            return None

    def _on_ocr_attachment_selected(self, _event: tk.Event | None = None) -> None:
        attachment_id = self._ocr_selected_attachment_id()
        if attachment_id is not None:
            self._load_ocr_editor_attachment(attachment_id)

    def refresh_ocr_tab(self) -> None:
        if not hasattr(self, "ocr_text"):
            return
        previous_id = self._ocr_selected_attachment_id()
        self.ocr_text.delete("1.0", "end")
        self.ocr_attachment_combo.configure(values=[])
        self.ocr_attachment_var.set("")
        if self.current_item_id is None:
            self.ocr_info_var.set("Selecciona una nota para ver el OCR reconocido.")
            self.ocr_text.insert("1.0", "Selecciona una nota para ver el OCR reconocido.")
            return
        rows = self.repo.list_attachments(self.current_item_id)
        values = []
        selected_id = None
        for row in rows:
            raw, corrected, active = self._attachment_ocr_texts(row)
            status = self._format_ocr_status(row)
            if not raw and not corrected and not status:
                continue
            filename = str(row["original_filename"] or row["stored_filename"] or "Adjunto")
            value = f"{int(row['id'])} | {filename}"
            values.append(value)
            if previous_id == int(row["id"]):
                selected_id = previous_id
            elif selected_id is None:
                selected_id = int(row["id"])
        self.ocr_attachment_combo.configure(values=values)
        if selected_id is None:
            self.ocr_info_var.set("Esta nota no tiene OCR guardado en sus adjuntos.")
            self.ocr_text.insert("1.0", "Esta nota no tiene OCR guardado en sus adjuntos.")
            return
        for value in values:
            if value.startswith(f"{selected_id} | "):
                self.ocr_attachment_var.set(value)
                break
        self._load_ocr_editor_attachment(selected_id)

    def _load_ocr_editor_attachment(self, attachment_id: int) -> None:
        row = self.repo.get_attachment(attachment_id)
        self.ocr_text.delete("1.0", "end")
        if row is None:
            self.ocr_info_var.set("Adjunto no encontrado.")
            return
        raw, corrected, active = self._attachment_ocr_texts(row)
        origin = get_effective_ocr_origin(row)
        status = self._format_ocr_status(row)
        updated_at = str(row["ocr_updated_at"] or "") if "ocr_updated_at" in row.keys() else ""
        corrected_at = str(row["ocr_corrected_at"] or "") if "ocr_corrected_at" in row.keys() else ""
        filename = str(row["original_filename"] or row["stored_filename"] or "Adjunto")
        mode = str(row["ocr_mode"] or "") if "ocr_mode" in row.keys() else ""
        rotation = row["ocr_rotation"] if "ocr_rotation" in row.keys() else None
        stored_chars = row["ocr_characters"] if "ocr_characters" in row.keys() else None
        chars = len(active)
        self.ocr_info_var.set(
            f"{filename} | Estado: {status or 'sin OCR'} | Origen: {origin or '—'} | Modo OCR: {mode or '—'} | "
            f"Rotación: {rotation if rotation is not None else '—'}° | Caracteres: {chars} | "
            f"Fecha OCR: {updated_at or '—'} | Corregido: {'sí' if corrected.strip() else 'no'}{f' ({corrected_at})' if corrected_at else ''}"
        )
        self.ocr_text.insert("1.0", active or "")

    def save_ocr_tab_correction(self) -> None:
        attachment_id = self._ocr_selected_attachment_id()
        if attachment_id is None:
            messagebox.showwarning("OCR Knowledge", "Selecciona un adjunto OCR.", parent=self)
            return
        text = self.ocr_text.get("1.0", "end-1c")
        result = self.repo.save_attachment_ocr_correction(attachment_id, text)
        if not result.get("ok"):
            messagebox.showerror("OCR Knowledge", str(result.get("message") or "No se pudo guardar."), parent=self)
            return
        self.status_var.set(f"Corrección OCR guardada: {int(result.get('chars') or 0)} caracteres")
        self.refresh_attachments(); self.refresh_ocr_tab(); self.refresh_items()

    def restore_ocr_tab_raw(self) -> None:
        attachment_id = self._ocr_selected_attachment_id()
        if attachment_id is None:
            return
        row = self.repo.get_attachment(attachment_id)
        if row is None:
            return
        raw, _corrected, _active = self._attachment_ocr_texts(row)
        self.ocr_text.delete("1.0", "end")
        self.ocr_text.insert("1.0", raw)
        logger.info("KNOWLEDGE_OCR: raw restored attachment_id=%s", attachment_id)

    def copy_ocr_tab_text(self) -> None:
        self.clipboard_clear(); self.clipboard_append(self.ocr_text.get("1.0", "end-1c")); self.status_var.set("Texto OCR copiado.")

    def select_ocr_zone_placeholder(self) -> None:
        messagebox.showinfo(
            "Seleccionar zona OCR",
            "Opción futura: permitirá recortar manualmente solo la etiqueta o ticket antes de lanzar OCR.",
            parent=self,
        )

    def rerun_ocr_tab_attachment(self) -> None:
        attachment_id = self._ocr_selected_attachment_id()
        if attachment_id is None:
            messagebox.showwarning("OCR Knowledge", "Selecciona un adjunto OCR.", parent=self); return
        answer = messagebox.askyesnocancel(
            "OCR / Mejorar",
            "¿Quieres rehacer OCR local o mejorar con IA?\n\nSí = Rehacer local\nNo = Mejorar con IA\nCancelar = Cancelar",
            parent=self,
        )
        if answer is None:
            return
        if answer is False:
            self._start_ai_ocr_attachment(attachment_id)
            return
        self._rerun_ocr_attachment_with_correction_prompt(attachment_id)

    def view_selected_attachment_ocr(self) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is None:
            messagebox.showwarning("OCR Knowledge", "Selecciona un adjunto para ver su OCR.", parent=self); return
        self._open_ocr_popup(attachment_id)

    def _open_ocr_popup(self, attachment_id: int) -> None:
        row = self.repo.get_attachment(attachment_id)
        if row is None:
            self.refresh_attachments(); return
        raw, corrected, active = self._attachment_ocr_texts(row)
        origin = get_effective_ocr_origin(row)
        status = self._format_ocr_status(row)
        updated_at = str(row["ocr_updated_at"] or "") if "ocr_updated_at" in row.keys() else ""
        filename = str(row["original_filename"] or row["stored_filename"] or "Adjunto")
        popup = tk.Toplevel(self); popup.title(f"OCR - {filename}"); popup.geometry("820x560")
        mode = str(row["ocr_mode"] or "") if "ocr_mode" in row.keys() else ""
        rotation = row["ocr_rotation"] if "ocr_rotation" in row.keys() else None
        stored_chars = row["ocr_characters"] if "ocr_characters" in row.keys() else None
        chars = len(active)
        info = ttk.Label(
            popup,
            text=(
                f"{filename} | Estado: {status or 'sin OCR'} | Origen: {origin or '—'} | Modo OCR: {mode or '—'} | "
                f"Rotación: {rotation if rotation is not None else '—'}° | Caracteres: {chars} | "
                f"Fecha OCR: {updated_at or '—'} | Corregido: {'sí' if corrected.strip() else 'no'}"
            ),
        )
        info.pack(fill="x", padx=10, pady=(10, 4))
        viewer = ScrolledText(popup, wrap="word"); viewer.pack(fill="both", expand=True, padx=10, pady=(0, 10)); viewer.insert("1.0", active)
        buttons = ttk.Frame(popup); buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="Guardar corrección", command=lambda: self._save_popup_ocr_correction(attachment_id, viewer, popup)).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="OCR / Mejorar", command=lambda: self._rerun_ocr_attachment_with_correction_prompt(attachment_id)).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Restaurar OCR bruto", command=lambda: (viewer.delete("1.0", "end"), viewer.insert("1.0", raw), logger.info("KNOWLEDGE_OCR: raw restored attachment_id=%s", attachment_id))).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Copiar", command=lambda: (self.clipboard_clear(), self.clipboard_append(viewer.get("1.0", "end-1c")))).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Cerrar", command=popup.destroy).pack(side="right")

    def _save_popup_ocr_correction(self, attachment_id: int, viewer: ScrolledText, popup: tk.Toplevel) -> None:
        result = self.repo.save_attachment_ocr_correction(attachment_id, viewer.get("1.0", "end-1c"))
        if result.get("ok"):
            self.refresh_attachments(); self.refresh_ocr_tab(); self.refresh_items(); popup.destroy()
        else:
            messagebox.showerror("OCR Knowledge", str(result.get("message") or "No se pudo guardar."), parent=popup)

    def _rerun_ocr_attachment_with_correction_prompt(self, attachment_id: int) -> None:
        row = self.repo.get_attachment(attachment_id)
        if row is None:
            return
        corrected = str(row["ocr_text_corrected"] or "") if "ocr_text_corrected" in row.keys() else ""
        replace = False
        if corrected.strip():
            answer = messagebox.askyesnocancel("Rehacer OCR", "Ya existe OCR corregido. ¿Quieres reemplazarlo por el nuevo OCR?\n\nSí = Reemplazar corrección\nNo = Mantener corrección\nCancelar = Cancelar", parent=self)
            if answer is None:
                return
            replace = bool(answer)
        if getattr(self, "_ocr_running", False):
            return
        self._ocr_running = True; self.status_var.set("Rehaciendo OCR..."); self.configure(cursor="watch")
        threading.Thread(target=self._run_rerun_ocr_worker, args=(attachment_id, replace), daemon=True).start()

    def _run_rerun_ocr_worker(self, attachment_id: int, replace_correction: bool) -> None:
        result = self.repo.ocr_attachment(attachment_id, force=True)
        if replace_correction and result.get("ok"):
            row = self.repo.get_attachment(attachment_id)
            raw = str(row["ocr_text_raw"] or row["ocr_text"] or "") if row is not None else ""
            self.repo.save_attachment_ocr_correction(attachment_id, raw)
        self.after(0, self._finish_ocr, result, None)

    def _format_ocr_status(self, row: sqlite3.Row | dict[str, object] | object) -> str:
        status = self._get_ocr_status_value(row)
        if status is None:
            return ""
        normalized_status = str(status).strip().lower()
        if normalized_status == "ok_ai" and get_effective_ocr_origin(row) != "IA":
            return "empty IA"
        return {
            "ok": "ok local",
            "ok_local": "ok local",
            "low_quality": "low quality",
            "ok_ai": "ok IA",
            "empty_ai": "empty IA",
            "error_ai": "error IA",
            "empty": "sin texto",
            "sin texto": "sin texto",
            "error": "error",
            "pending": "pendiente",
            "pendiente": "pendiente",
            "corrected": "corregido",
            "running": "en curso",
            "skipped": "omitido",
            "unavailable": "no disponible",
            "ignored": "ignorado",
        }.get(normalized_status, "")

    @staticmethod
    def _get_ocr_status_value(row: sqlite3.Row | dict[str, object] | object) -> object | None:
        if row is None:
            return None
        if isinstance(row, dict):
            return row.get("ocr_status")
        keys = getattr(row, "keys", None)
        if callable(keys):
            try:
                if "ocr_status" in keys():
                    return row["ocr_status"]
                return None
            except (KeyError, IndexError, TypeError):
                return None
        return getattr(row, "ocr_status", None)

    def _run_ocr_worker(self, target: str, identifier: int) -> None:
        try:
            if target == "attachment":
                result = self.repo.ocr_attachment(identifier)
            elif target == "attachment_force":
                result = self.repo.ocr_attachment(identifier, force=True)
            else:
                result = self.repo.ocr_item_attachments(identifier)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_OCR: UI worker failed")
            try:
                self.after(0, self._finish_ocr, None, exc)
            except tk.TclError:
                pass
            return
        try:
            self.after(0, self._finish_ocr, result, None)
        except tk.TclError:
            logger.info("KNOWLEDGE_OCR: ventana cerrada antes de mostrar resultado")

    def _finish_ocr(self, result: dict[str, object] | None, error: Exception | None) -> None:
        self.configure(cursor="")
        self._ocr_running = False
        self.refresh_attachments()
        self.refresh_ocr_tab()
        self.refresh_items()
        if error is not None or result is None:
            message = f"No se pudo ejecutar OCR. {error}"
            self.status_var.set(message)
            messagebox.showerror("OCR Knowledge", message, parent=self)
            return
        if "total" in result:
            message = (
                f"OCR finalizado: {int(result.get('ok') or 0)} adjunto(s) con texto, "
                f"{int(result.get('empty') or 0)} sin texto, {int(result.get('errors') or 0)} errores."
            )
        else:
            status = str(result.get("status") or "")
            chars = int(result.get("chars") or 0)
            if status == "unavailable":
                message = str(result.get("message") or "OCR no disponible. Instala Tesseract OCR y pytesseract.")
                messagebox.showwarning("OCR Knowledge", message, parent=self)
            elif status in {"empty_ai", "error_ai", "error"}:
                message = str(result.get("message") or "La IA no ha podido extraer texto útil.")
                messagebox.showwarning("OCR Knowledge", message, parent=self)
            elif status in {"empty", "low_quality"}:
                message = "OCR local insuficiente. Queda pendiente."
                answer = messagebox.askyesnocancel(
                    "OCR / Mejorar",
                    "El OCR local no ha obtenido texto suficiente.\n¿Quieres intentar reconocimiento con IA?\n\nSí = Usar IA\nNo = Dejar pendiente\nCancelar = Ignorar OCR",
                    parent=self,
                )
                attachment_id = int(result.get("attachment_id") or self._selected_attachment_id() or 0)
                if answer is True and attachment_id:
                    self._start_ai_ocr_attachment(attachment_id)
                elif answer is None and attachment_id:
                    self.repo.ignore_attachment_ocr(attachment_id); self.refresh_attachments(); self.refresh_ocr_tab()
            elif status in {"ok", "ok_local", "ok_ai"}:
                message = f"OCR finalizado: {chars} caracteres"
            else:
                message = str(result.get("message") or "OCR finalizado")
        self.status_var.set(message)
        if "total" in result:
            messagebox.showinfo("OCR Knowledge", message, parent=self)

    def ocr_selected_attachment(self) -> None:
        attachment_id = self._selected_attachment_id()
        if attachment_id is None:
            messagebox.showwarning("OCR Knowledge", "Selecciona un adjunto para ejecutar OCR.", parent=self)
            return
        row = self.repo.get_attachment(attachment_id)
        status = str(row["ocr_status"] or "").lower() if row is not None else ""
        if status in {"ok", "ok_local", "ok_ai", "corrected"}:
            answer = messagebox.askyesnocancel(
                "OCR / Mejorar",
                "Este adjunto ya tiene OCR. ¿Quieres rehacer OCR local o mejorar con IA?\n\nSí = Rehacer local\nNo = Mejorar con IA\nCancelar = Cancelar",
                parent=self,
            )
            if answer is None:
                return
            if answer is False:
                self._start_ai_ocr_attachment(attachment_id)
                return
            self._start_local_ocr_attachment(attachment_id, force=True)
            return
        self._start_local_ocr_attachment(attachment_id, force=False)

    def _start_local_ocr_attachment(self, attachment_id: int, *, force: bool = False) -> None:
        if getattr(self, "_ocr_running", False):
            return
        self._ocr_running = True
        self.status_var.set("OCR local en curso...")
        self.configure(cursor="watch")
        threading.Thread(target=self._run_ocr_worker, args=("attachment_force" if force else "attachment", attachment_id), daemon=True).start()

    def _start_ai_ocr_attachment(self, attachment_id: int) -> None:
        if getattr(self, "_ocr_running", False):
            return
        self._ocr_running = True
        self.status_var.set("OCR con IA en curso...")
        self.configure(cursor="watch")
        threading.Thread(target=self._run_ai_ocr_worker, args=(attachment_id,), daemon=True).start()

    def _run_ai_ocr_worker(self, attachment_id: int) -> None:
        try:
            result = self.repo.improve_attachment_ocr_with_ai(attachment_id)
            self.after(0, self._finish_ocr, result, None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_OCR: AI UI worker failed")
            self.after(0, self._finish_ocr, None, exc)

    def ocr_current_note_attachments(self) -> None:
        if self.current_item_id is None:
            messagebox.showwarning("OCR Knowledge", "Selecciona una nota para ejecutar OCR.", parent=self)
            return
        if getattr(self, "_ocr_running", False):
            return
        self._ocr_running = True
        self.status_var.set("OCR en curso...")
        self.configure(cursor="watch")
        threading.Thread(target=self._run_ocr_worker, args=("note", self.current_item_id), daemon=True).start()

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

    def _open_file_with_default_app(self, path: Path) -> bool:
        try:
            logger.info("KNOWLEDGE_ATTACHMENT: abriendo archivo path=%s", path)
            open_file_with_default_app(path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_ATTACHMENT: error abriendo archivo path=%s", path)
            messagebox.showerror("Abrir adjunto", f"No se pudo abrir el archivo.\n\n{exc}", parent=self)
            return False

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
        if self._open_file_with_default_app(path):
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
        if self._open_file_with_default_app(folder):
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
