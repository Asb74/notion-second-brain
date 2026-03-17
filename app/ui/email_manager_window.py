"""Email manager Tkinter window."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import base64
import csv
import threading
import shutil
import sqlite3
import tempfile
import tkinter as tk
from datetime import datetime
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Callable
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from tkcalendar import DateEntry

from app.config.mail_config import USER_EMAIL
from app.core.email.category_manager import CategoryManager
from app.core.email.gmail_client import GmailClient
from app.core.email.attachment_cache import AttachmentCache
from app.core.email.mail_ingestion_service import MailIngestionService
from app.core.models import NoteCreateRequest
from app.core.outlook.outlook_service import OutlookService
from app.core.service import NoteService
from app.persistence.email_repository import EmailRepository
from app.persistence.calendar_repository import CalendarRepository
from app.persistence.training_repository import TrainingRepository
from app.ml.continuous_learning_service import ContinuousLearningService
from app.ml.global_learning_store import GlobalLearningStore
from app.ml.ml_training_manager import MLTrainingManager
from app.ml.retraining_service import DatasetRetrainingService
from app.persistence.user_profile_repository import UserProfileRepository
from app.services.email_entity_extractor import EmailEntityExtractor
from app.ui.app_icons import apply_app_icon
from app.ui.excel_filter import ExcelTreeFilter
from app.ui.dictation_widgets import attach_dictation
from app.ui.refinement_panel import (
    REFINEMENT_MODE_ATTACHMENT_SUMMARY,
    REFINEMENT_MODE_EMAIL_SUMMARY,
    REFINEMENT_MODE_RESPONSE,
    RefinamientoPanel,
)
from app.utils.openai_client import MODEL_NAME, build_openai_client
from app.utils.attachment_text_extractor import (
    MAX_ATTACHMENT_TEXT,
    SUPPORTED_ATTACHMENT_EXTENSIONS,
    extract_text_from_attachments,
)

logger = logging.getLogger(__name__)

def _sanitize_tk_color(color: str | None, fallback: str = "#000000") -> str:
    """Return a Tkinter-safe color value for known invalid system color aliases."""
    value = str(color or "").strip()
    if not value:
        return fallback
    if value.lower() == "windowtext":
        return fallback
    return value


def _sanitize_html_colors(html_content: str) -> str:
    """Replace Tk-unsupported HTML system color names with safe equivalents."""
    sanitized = html_content or ""
    replacements = {
        "windowtext": "black",
        "window": "white",
        "buttontext": "black",
        "buttonface": "lightgray",
    }

    for source, target in replacements.items():
        sanitized = re.sub(rf"\\b{re.escape(source)}\\b", target, sanitized, flags=re.IGNORECASE)

    return sanitized


ATTACHMENT_SUMMARY_REQUEST = (
    "Analiza el contenido consolidado de adjuntos y devuelve un resumen accionable en español.\n"
    "Reglas:\n"
    "- máximo 8 líneas\n"
    "- usar viñetas (•)\n"
    "- destacar datos, fechas, importes y riesgos relevantes\n"
    "- no inventar información\n"
    "- si falta contexto, indícalo brevemente\n"
)
MAX_REFINEMENTS = 5
EMAIL_SUMMARY_EXAMPLES_LIMIT = 5

_SYSTEM_LOG_WIDGET: ScrolledText | None = None


def is_table(text: str) -> bool:
    """Return True when text resembles a Markdown or CSV table."""
    normalized_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(normalized_lines) < 2:
        return False

    pipe_lines = [line for line in normalized_lines if "|" in line]
    if len(pipe_lines) >= 2:
        return True

    comma_lines = [line for line in normalized_lines if "," in line]
    if len(comma_lines) < 2:
        return False

    expected_columns = len([cell for cell in comma_lines[0].split(",")])
    if expected_columns < 2:
        return False
    return all(len(line.split(",")) == expected_columns for line in comma_lines[:6])


def clean_markdown(text: str) -> str:
    """Remove common Markdown markers from a text fragment."""
    sanitized = re.sub(r"\*\*(.*?)\*\*", r"\1", text or "")
    sanitized = sanitized.replace("**", "")
    return sanitized.strip()


def export_to_csv(headers: list[str], rows: list[list[str]], path: str) -> None:
    """Persist table data to CSV with explicit headers and UTF-8 encoding."""
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)


def copiar_tabla(headers: list[str], rows: list[list[str]]) -> None:
    """Copy table data to clipboard in a format compatible with Excel."""
    import pandas as pd

    df = pd.DataFrame(rows, columns=headers)
    df.to_clipboard(index=False)


def parse_markdown_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse Markdown/CSV tabular text and return headers and rows."""
    normalized_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]

    if any("|" in line for line in normalized_lines):
        candidate_lines = []
        for line in normalized_lines:
            compact = line.replace("|", "").replace(":", "").replace("-", "").strip()
            if not compact:
                continue
            candidate_lines.append(line)

        parsed_rows: list[list[str]] = []
        for line in candidate_lines:
            trimmed = line.strip("|")
            columns = [cell.strip() for cell in trimmed.split("|")]
            if columns:
                parsed_rows.append(columns)
    else:
        parsed_rows = [next(csv.reader([line])) for line in normalized_lines if "," in line]

    if not parsed_rows:
        return [], []

    max_columns = max(len(row) for row in parsed_rows)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in parsed_rows]
    headers = [clean_markdown(header) for header in normalized_rows[0]]
    rows = [[clean_markdown(cell) for cell in row] for row in normalized_rows[1:]]
    return headers, rows


def detect_kv_format(text: str) -> bool:
    """Detect repeated key:value structures across multiple lines."""
    normalized_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(normalized_lines) < 3:
        return False

    kv_lines = 0
    for line in normalized_lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        cleaned_key = clean_markdown(key).strip("-•* ").rstrip(":").strip()
        cleaned_value = clean_markdown(value)
        if cleaned_key and cleaned_value:
            kv_lines += 1

    return kv_lines >= 2


def parse_kv_to_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse noisy key:value text into a normalized 2-column table."""
    cleaned_text = clean_markdown(text)
    rows: list[list[str]] = []

    for line in cleaned_text.splitlines():
        candidate = line.strip()
        if not candidate or ":" not in candidate:
            continue

        key, value = candidate.split(":", 1)
        normalized_key = re.sub(r"\s+", " ", clean_markdown(key)).strip("-•* ").replace(":", "").strip()
        normalized_value = re.sub(r"\s+", " ", clean_markdown(value)).lstrip(":").strip()
        if not normalized_key or not normalized_value:
            continue

        rows.append([normalized_key, normalized_value])

    return ["Campo", "Detalle"], rows


def normalize_to_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    """Normalize markdown/csv/kv structures into a renderable table."""
    if is_table(text):
        headers, rows = parse_markdown_table(text)
        if headers and rows:
            return headers, rows

    if detect_kv_format(text):
        headers, rows = parse_kv_to_table(text)
        if rows:
            return headers, rows

    return None


def render_table(parent: tk.Misc, headers: list[str], rows: list[list[str]]) -> ttk.Treeview:
    """Render headers and rows into a table-like Treeview."""
    tree = ttk.Treeview(parent, columns=headers, show="headings")
    for col in headers:
        tree.heading(col, text=col)
        tree.column(col, anchor="w", width=150)

    for row in rows:
        values = row + [""] * (len(headers) - len(row))
        tree.insert("", "end", values=values[: len(headers)])

    tree.pack(fill="both", expand=True)
    return tree


def is_real_html(content: str | None) -> bool:
    """Return True when the input includes actual structural HTML tags."""
    if not content:
        return False

    lowered = content.lower()

    html_tags = [
        "<html",
        "<body",
        "<table",
        "<div",
        "<p>",
        "<br>",
        "<tr>",
        "<td>",
    ]

    return any(tag in lowered for tag in html_tags)


def clean_outlook_content(text: str | None) -> str:
    """Strip Word/Outlook CSS noise and keep only the meaningful message body."""
    if not text:
        return ""

    cleaned = re.sub(r"\{mso-[^}]+\}", "", text)
    cleaned = re.sub(r"@list[^;]+;", "", cleaned, flags=re.IGNORECASE)

    cleaned_lines: list[str] = []
    for line in cleaned.splitlines():
        lowered_line = line.lower()
        if "mso-" in lowered_line:
            continue
        if "wordsection" in lowered_line:
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    header_match = re.search(r"(de:|from:)", cleaned, flags=re.IGNORECASE)
    if header_match:
        cleaned = cleaned[header_match.start():]

    return cleaned.strip()


def strip_outlook_word_html(html_content: str) -> str:
    """Remove Outlook Word CSS blocks and style tags."""
    if not html_content:
        return ""

    html_content = re.sub(
        r"<style[^>]*>.*?</style>",
        "",
        html_content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    html_content = re.sub(
        r"<!--\[if.*?endif\]-->",
        "",
        html_content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return html_content.strip()


def clean_outlook_styles(text: str | None) -> str:
    """Backward-compatible alias for the Outlook plain-text cleaner."""
    return clean_outlook_content(text)


def system_log(message: str, level: str = "INFO") -> None:
    """Write timestamped messages into the system status panel."""
    if _SYSTEM_LOG_WIDGET is None:
        return

    timestamp = datetime.now().strftime("%H:%M:%S")
    normalized_level = (level or "INFO").upper()
    _SYSTEM_LOG_WIDGET.configure(state="normal")
    _SYSTEM_LOG_WIDGET.insert("end", f"[{timestamp}][{normalized_level}] {message}\n")
    _SYSTEM_LOG_WIDGET.see("end")
    _SYSTEM_LOG_WIDGET.configure(state="disabled")


def build_email_training_input_text(subject: str, sender: str, body_text: str) -> str:
    return (
        "EMAIL_SUBJECT:\n"
        f"{(subject or '').strip()}\n\n"
        "EMAIL_SENDER:\n"
        f"{(sender or '').strip()}\n\n"
        "EMAIL_BODY:\n"
        f"{(body_text or '').strip()}"
    ).strip()


class EmailManagerWindow(tk.Toplevel):
    """Manage ingested emails and manual conversion to notes."""

    def __init__(
        self,
        master: tk.Misc,
        note_service: NoteService,
        db_connection: sqlite3.Connection,
        gmail_client: GmailClient,
    ):
        super().__init__(master)
        self.note_service = note_service
        self.gmail_client = gmail_client
        self.email_repo = EmailRepository(db_connection)
        self.calendar_repo = CalendarRepository(db_connection)
        self.training_repo = TrainingRepository(db_connection)
        self.user_profile_repo = UserProfileRepository(db_connection)
        self.category_manager = CategoryManager(self.email_repo)
        self.mail_ingestion_service = MailIngestionService(gmail_client=gmail_client, db_connection=db_connection)
        self.classifier = self.mail_ingestion_service.classifier
        self.retraining_service = DatasetRetrainingService(db_connection, self.email_repo)
        self.continuous_learning_service = ContinuousLearningService(
            db_connection=db_connection,
            email_classifier=self.classifier,
        )
        dataset_dir = Path(os.getenv("ML_DATASET_DIR", "data/ml_datasets"))
        self.ml_training_manager = MLTrainingManager(base_dir=dataset_dir)
        global_training_path = Path(os.getenv("GLOBAL_TRAINING_DATA_PATH", "training_data.json"))
        self.global_learning_store = GlobalLearningStore(file_path=global_training_path)
        self.outlook_service = OutlookService()
        self.attachment_cache = AttachmentCache(gmail_client=gmail_client)
        self.my_email = self._resolve_my_email()

        self.title("Gestión de Emails")
        apply_app_icon(self)
        self.geometry("1220x760")
        self.minsize(1080, 620)

        self.status_var = tk.StringVar(value="Listo")
        self.model_var = tk.StringVar(value=self.classifier.model_status())
        self._categories = self.category_manager.list_categories()
        default_label = self._categories[0]["display_name"] if self._categories else "Otros"
        self._tab_to_types = {item["display_name"]: [item["name"]] for item in self._categories}
        self._move_label_to_type = {item["display_name"]: item["name"] for item in self._categories}
        self._category_counts_by_type: dict[str, int] = {}
        self.move_target_var = tk.StringVar(value=default_label)
        self.columns = ("gmail_id", "subject", "real_sender", "type", "received_at", "status")
        self.column_titles = {
            "gmail_id": "Gmail ID",
            "subject": "Asunto",
            "real_sender": "Remitente",
            "type": "Tipo",
            "received_at": "Fecha",
            "status": "Estado",
        }
        self._all_rows: list[dict[str, str]] = []
        self._attachments_buttons: list[ttk.Button] = []
        self._rows_by_id: dict[str, dict[str, str]] = {}
        self._current_tab = default_label
        self._current_html_content = ""
        self._expanded_html_window: tk.Toplevel | None = None
        self._expanded_html_frame: ScrolledText | None = None
        self.preview_html: tk.Widget | None = None
        self._expanded_attachment_preview_window: tk.Toplevel | None = None
        self._expanded_attachment_preview_frame: tk.Widget | None = None
        self._temp_opened_attachments: list[Path] = []
        self._temp_forward_attachments_dirs: list[Path] = []
        self._logs_visible = True
        self._logs_frame: ttk.LabelFrame | None = None
        self._main_paned: ttk.PanedWindow | None = None
        self.detected_pedido_var = tk.StringVar(value="")
        self.detected_cliente_var = tk.StringVar(value="")
        self.detected_persona_var = tk.StringVar(value="")
        self.detected_accion_var = tk.StringVar(value="")
        self._pending_note_id_by_gmail_id: dict[str, int] = {}
        self._prepared_context_by_gmail_id: dict[str, dict[str, str]] = {}
        self.calendar_refresh_callback: Callable[[], None] | None = None
        self.tree_context_menu: tk.Menu | None = None

        self._build_layout()
        self.refresh_emails()

    @staticmethod
    def _clear_result_container(container: tk.Misc) -> None:
        for widget in container.winfo_children():
            widget.destroy()

    def _copy_treeview_as_csv(self, tree: ttk.Treeview) -> None:
        headers = [str(column) for column in tree["columns"]]
        rows: list[list[str]] = []
        for item in tree.get_children():
            rows.append([str(value) for value in tree.item(item, "values")])

        copiar_tabla(headers, rows)
        messagebox.showinfo("Tabla", "Tabla copiada al portapapeles.")

    def _save_treeview_as_csv(self, tree: ttk.Treeview) -> None:
        file_path = filedialog.asksaveasfilename(
            title="Guardar tabla",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Todos los archivos", "*.*")],
        )
        if not file_path:
            return

        headers = [str(column) for column in tree["columns"]]
        rows = [list(tree.item(item, "values")) for item in tree.get_children()]
        export_to_csv(headers, rows, file_path)

        messagebox.showinfo("Tabla", f"Tabla guardada en:\n{file_path}")

    def _render_output_widget(self, container: tk.Misc, output_text: str) -> tk.Text | None:
        self._clear_result_container(container)
        clean_output = clean_markdown(output_text)

        normalized_table = normalize_to_table(clean_output)
        if normalized_table:
            headers, rows = normalized_table
            wrapper = ttk.Frame(container)
            wrapper.pack(fill="both", expand=True)
            wrapper.rowconfigure(0, weight=1)
            wrapper.columnconfigure(0, weight=1)

            table_frame = ttk.Frame(wrapper)
            table_frame.grid(row=0, column=0, sticky="nsew")
            table_frame.rowconfigure(0, weight=1)
            table_frame.columnconfigure(0, weight=1)

            tree = render_table(table_frame, headers, rows)
            tree.grid(row=0, column=0, sticky="nsew")
            y_scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
            y_scrollbar.grid(row=0, column=1, sticky="ns")
            x_scrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
            x_scrollbar.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)

            actions = ttk.Frame(wrapper)
            actions.grid(row=1, column=0, sticky="ew", pady=(6, 0))
            ttk.Button(actions, text="📋 Copiar tabla", command=lambda: self._copy_treeview_as_csv(tree)).pack(side="left")
            ttk.Button(actions, text="💾 Guardar como CSV", command=lambda: self._save_treeview_as_csv(tree)).pack(side="left", padx=(6, 0))
            return None

        editor = ScrolledText(container, wrap="word", height=14)
        editor.pack(fill="both", expand=True)
        editor.insert("1.0", clean_output)
        return editor

    def _build_layout(self) -> None:
        global _SYSTEM_LOG_WIDGET

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))
        style.configure("Chip.TFrame", background="#EEF1F5", borderwidth=1, relief="solid", padding=(6, 3))
        style.configure("ChipText.TLabel", background="#EEF1F5")
        style.configure("ChipClose.TButton", padding=(2, 0))
        style.map(
            "Treeview",
            background=[("selected", _sanitize_tk_color("#2E6BD1"))],
            foreground=[("selected", _sanitize_tk_color("white"))],
        )

        # Arquitectura UX base reutilizable:
        # 1) Menú superior para acciones globales
        # 2) Toolbar para acciones rápidas y frecuentes
        # 3) Área de trabajo principal con paneles + pestañas
        self._build_menu_bar()

        toolbar_container = ttk.Frame(self, padding=(10, 10, 10, 6))
        toolbar_container.pack(fill="x")
        self._build_quick_toolbar(toolbar_container)

        self._main_paned = ttk.PanedWindow(self, orient="vertical")
        self._main_paned.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        content_zone = ttk.Frame(self._main_paned)
        self._logs_frame = ttk.LabelFrame(self._main_paned, text="Estado / Logs")
        self._main_paned.add(content_zone, weight=7)
        self._main_paned.add(self._logs_frame, weight=1)

        # Layout principal UX: panel izquierdo de gestión y panel derecho de detalle dinámico.
        content_paned = ttk.PanedWindow(content_zone, orient="horizontal")
        content_paned.pack(fill="both", expand=True)

        frame_left = ttk.Frame(content_paned, padding=(0, 0, 8, 0))
        frame_right = ttk.Frame(content_paned)
        content_paned.add(frame_left, weight=1)
        content_paned.add(frame_right, weight=3)

        frame_left.rowconfigure(3, weight=1)
        frame_left.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(frame_left)
        self._rebuild_tabs()
        self.notebook.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook.bind("<Button-3>", self._open_tab_context_menu)

        self.tab_menu = tk.Menu(self, tearoff=0)
        self.tab_menu.add_command(label="Renombrar categoría", command=self._rename_current_category)
        self.tab_menu.add_command(label="Eliminar categoría", command=self._delete_current_category)

        filters_row = ttk.Frame(frame_left)
        filters_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.filters_menu_button = ttk.Menubutton(filters_row, text="Filtros", style="Toolbar.TButton")
        self.filters_menu = tk.Menu(self.filters_menu_button, tearoff=0)
        self.filters_menu.add_command(label="Solo no leídos", command=self._select_unread_rows)
        self.filters_menu.add_command(label="Solo pedidos", command=lambda: self._select_rows_by_type("order"))
        self.filters_menu.add_command(label="Solo suscripciones", command=lambda: self._select_rows_by_type("subscription"))
        self.filters_menu_button.configure(menu=self.filters_menu)
        self.filters_menu_button.pack(side="left", padx=(0, 6))
        ttk.Button(filters_row, text="Limpiar filtros", command=self._clear_filters).pack(side="left")

        selection_row = ttk.Frame(frame_left)
        selection_row.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(selection_row, text="Seleccionar todo", command=self._select_all_rows).pack(side="left")
        ttk.Button(selection_row, text="Deseleccionar todo", command=self._clear_selection).pack(side="left", padx=(6, 0))

        self.move_target_combo = ttk.Combobox(
            selection_row,
            textvariable=self.move_target_var,
            values=list(self._move_label_to_type.keys()),
            state="readonly",
            width=16,
        )
        self.move_target_combo.pack(side="left", padx=(12, 6))
        ttk.Button(selection_row, text="Aplicar", command=self._move_selected_emails).pack(side="left")

        table_frame = ttk.Frame(frame_left)
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(table_frame, columns=self.columns, show="headings", selectmode="extended")
        self.tree.tag_configure("email_new", background=_sanitize_tk_color("#E8F4FF"), font=("Segoe UI", 9, "bold"))
        self.tree.tag_configure("email_ignored", foreground=_sanitize_tk_color("#999999"))
        self.tree.tag_configure("email_converted", background=_sanitize_tk_color("#E8FFE8"))
        self.tree.tag_configure("email_forwarded", background=_sanitize_tk_color("#FFF3E0"))
        for col in self.columns:
            self.tree.heading(col, text=self.column_titles.get(col, col))

        self.tree.column("gmail_id", width=170, anchor="w")
        self.tree.column("subject", width=260, anchor="w")
        self.tree.column("real_sender", width=190, anchor="w")
        self.tree.column("type", width=110, anchor="w")
        self.tree.column("received_at", width=140, anchor="w")
        self.tree.column("status", width=90, anchor="w")

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_preview())
        self.tree.bind("<Button-3>", self._show_tree_context_menu)

        self.tree_context_menu = tk.Menu(self.tree, tearoff=0)
        self.tree_context_menu.add_command(label="Marcar como ignorado", command=self._mark_selected_as_ignored)

        self.excel_filter = ExcelTreeFilter(
            master=self,
            tree=self.tree,
            columns=self.columns,
            column_titles=self.column_titles,
            get_rows=lambda: self._all_rows,
            set_rows=self._set_filtered_rows,
        )

        frame_right.rowconfigure(0, weight=1)
        frame_right.columnconfigure(0, weight=1)
        self.detail_notebook = ttk.Notebook(frame_right)
        self.detail_notebook.grid(row=0, column=0, sticky="nsew")

        preview_tab = ttk.Frame(self.detail_notebook)
        html_preview_frame = ttk.Frame(preview_tab, padding=4)
        html_preview_frame.pack(fill="both", expand=True)
        from tkhtmlview import HTMLScrolledText

        self.preview_html = HTMLScrolledText(
            html_preview_frame,
            html="",
            background=_sanitize_tk_color("white"),
        )
        self.preview_html.pack(fill="both", expand=True)

        preview_actions = ttk.Frame(preview_tab, padding=(4, 0, 4, 4))
        preview_actions.pack(fill="x")
        ttk.Button(preview_actions, text="Expandir vista", command=self._expand_html_view).pack(side="left")

        response_tab = ttk.Frame(self.detail_notebook)
        response_editor_container = ttk.Frame(response_tab)
        response_editor_container.pack(fill="both", expand=True)
        self.response_text = tk.Text(response_editor_container, wrap="word")
        response_scroll = ttk.Scrollbar(response_editor_container, orient="vertical", command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=response_scroll.set)
        self.response_text.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        response_scroll.pack(side="right", fill="y", padx=(0, 4), pady=4)

        response_controls = ttk.Frame(response_tab, padding=(4, 0, 4, 4))
        response_controls.pack(fill="x")
        self.response_dictation_controls = attach_dictation(self.response_text, response_controls)
        self.response_dictation_controls.pack(anchor="w", pady=(0, 4))

        response_actions = ttk.Frame(response_controls)
        response_actions.pack(fill="x")
        ttk.Button(response_actions, text="Generar respuesta", command=self._generate_response).pack(side="left", padx=(0, 6))
        ttk.Button(response_actions, text="Responder", command=self._create_outlook_draft).pack(side="left")
        ttk.Button(response_actions, text="Reenviar", command=self._forward_email).pack(side="left", padx=(6, 0))
        ttk.Button(response_actions, text="Resumir", command=self._summarize_email).pack(side="left", padx=(6, 0))
        ttk.Button(response_actions, text="Resumir adjuntos", command=self._summarize_attachments).pack(side="left", padx=(6, 0))
        ttk.Button(response_actions, text="Preparar contexto", command=self._prepare_context_for_selected_email).pack(side="left", padx=(6, 0))

        attachments_tab = ttk.Frame(self.detail_notebook)
        self.attachments_list = tk.Listbox(attachments_tab, exportselection=False)
        self.attachments_list.pack(fill="both", expand=True, padx=6, pady=(6, 2))
        attachments_actions = ttk.Frame(attachments_tab)
        attachments_actions.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(attachments_actions, text="Abrir", command=self._open_selected_attachment).pack(side="left")
        ttk.Button(attachments_actions, text="Guardar como…", command=self._save_selected_attachment).pack(side="left", padx=(6, 0))
        ttk.Button(attachments_actions, text="Descargar", command=self._download_selected_attachment).pack(side="left", padx=(6, 0))
        ttk.Button(attachments_actions, text="Adjuntar al borrador", command=self._attach_selected_to_draft).pack(side="left", padx=(6, 0))

        entities_tab = ttk.Frame(self.detail_notebook, padding=8)
        ttk.Label(entities_tab, text="Pedido:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, textvariable=self.detected_pedido_var).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, text="Cliente:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, textvariable=self.detected_cliente_var).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, text="Persona:").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, textvariable=self.detected_persona_var).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, text="Acción:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_tab, textvariable=self.detected_accion_var).grid(row=3, column=1, sticky="w", padx=4, pady=2)

        self.detail_notebook.add(preview_tab, text="Vista previa")
        self.detail_notebook.add(response_tab, text="Respuesta")
        self.detail_notebook.add(attachments_tab, text="Adjuntos")
        self.detail_notebook.add(entities_tab, text="Datos")

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status_frame, textvariable=self.model_var, anchor="e").pack(side="right")
        self._toggle_logs_button = ttk.Button(status_frame, text="Ocultar logs", command=self._toggle_logs_panel)
        self._toggle_logs_button.pack(side="right", padx=(8, 0))

        self.system_status_text = tk.Text(self._logs_frame, height=8, state="disabled", wrap="word")
        status_scroll = ttk.Scrollbar(self._logs_frame, orient="vertical", command=self.system_status_text.yview)
        self.system_status_text.configure(yscrollcommand=status_scroll.set)
        self.system_status_text.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        status_scroll.pack(side="right", fill="y", padx=(0, 6), pady=6)
        _SYSTEM_LOG_WIDGET = self.system_status_text

    def _build_menu_bar(self) -> None:
        menubar = tk.Menu(self)

        archivo = tk.Menu(menubar, tearoff=0)
        archivo.add_command(label="Nueva nota", command=self._create_notes_from_selected_emails)
        archivo.add_command(label="Abrir", command=self._refresh_preview)
        archivo.add_command(label="Guardar", command=self._create_outlook_draft)
        archivo.add_separator()
        archivo.add_command(label="Salir", command=self.destroy)
        menubar.add_cascade(label="Archivo", menu=archivo)

        edicion = tk.Menu(menubar, tearoff=0)
        edicion.add_command(label="Copiar", command=lambda: self._clipboard_event("<<Copy>>"))
        edicion.add_command(label="Pegar", command=lambda: self._clipboard_event("<<Paste>>"))
        edicion.add_command(label="Limpiar", command=self._clear_active_text_widget)
        menubar.add_cascade(label="Edición", menu=edicion)

        herramientas = tk.Menu(menubar, tearoff=0)
        herramientas.add_command(label="Descargar", command=self._download_new_emails)
        herramientas.add_command(label="Reentrenar modelo", command=self._retrain_model)
        herramientas.add_command(label="Reclasificar", command=self._reclassify_current_emails)
        herramientas.add_command(label="Marcar ignoradas", command=self._mark_selected_as_ignored)
        menubar.add_cascade(label="Herramientas", menu=herramientas)

        maestros = tk.Menu(menubar, tearoff=0)
        maestros.add_command(label="Perfiles", command=self._open_profiles_master)
        maestros.add_command(label="Contextos", command=self._create_category)
        maestros.add_command(label="Plantillas", command=self._open_templates_master)
        menubar.add_cascade(label="Maestros", menu=maestros)

        ia_menu = tk.Menu(menubar, tearoff=0)
        ia_menu.add_command(label="Generar respuesta", command=self._generate_response)
        ia_menu.add_command(label="Resumir", command=self._summarize_email)
        ia_menu.add_command(label="Preparar contexto", command=self._prepare_context_for_selected_email)
        menubar.add_cascade(label="IA", menu=ia_menu)

        self.config(menu=menubar)

    def _build_quick_toolbar(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x")

        quick_actions = [
            ("⬇ Descargar", self._download_new_emails),
            ("🧠 Reentrenar", self._retrain_model),
            ("🔁 Reclasificar", self._reclassify_current_emails),
            ("🙈 Marcar ignoradas", self._mark_selected_as_ignored),
            ("🏷 Nueva categoría", self._create_category),
            ("📝 Crear nota", self._create_notes_from_selected_emails),
            ("📅 Crear evento", self._create_events_from_selected_emails),
        ]
        for idx, (label, command) in enumerate(quick_actions):
            ttk.Button(toolbar, text=label, command=command, style="Toolbar.TButton").pack(side="left", padx=(0, 6))
            if idx < len(quick_actions) - 1:
                ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=(0, 6))

    def _clipboard_event(self, event_name: str) -> None:
        widget = self.focus_get()
        if widget is None:
            return
        try:
            widget.event_generate(event_name)
        except tk.TclError:
            return

    def _clear_active_text_widget(self) -> None:
        widget = self.focus_get()
        if isinstance(widget, (tk.Text, tk.Entry, ttk.Entry)):
            widget.delete(0 if isinstance(widget, (tk.Entry, ttk.Entry)) else "1.0", "end")

    def _open_profiles_master(self) -> None:
        messagebox.showinfo("Perfiles", "La gestión de perfiles se administra desde la ventana principal.")

    def _open_templates_master(self) -> None:
        messagebox.showinfo("Plantillas", "El módulo de plantillas se habilitará en una siguiente iteración.")

    def _toggle_logs_panel(self) -> None:
        if self._main_paned is None or self._logs_frame is None:
            return
        if self._logs_visible:
            try:
                self._main_paned.forget(self._logs_frame)
            except tk.TclError:
                pass
            self._logs_visible = False
            self._toggle_logs_button.configure(text="Mostrar logs")
            self.log("Panel de logs oculto")
            return
        self._main_paned.add(self._logs_frame, weight=1)
        self._logs_visible = True
        self._toggle_logs_button.configure(text="Ocultar logs")
        self.log("Panel de logs visible")

    def log(self, message: str, level: str = "INFO") -> None:
        self.status_var.set(message)
        system_log(message, level=level)

    def system_log(self, message: str, level: str = "INFO") -> None:
        self.log(message, level=level)

    def insert_text(self, text: str) -> None:
        """Append text in the response editor preserving existing content (dictation-ready)."""
        self.response_text.focus_set()
        self.response_text.insert("end", text)
        self.response_text.see("end")

    def _reload_category_maps(self) -> None:
        self._categories = self.category_manager.list_categories()
        self._tab_to_types = {item["display_name"]: [item["name"]] for item in self._categories}
        self._move_label_to_type = {item["display_name"]: item["name"] for item in self._categories}
        labels = list(self._move_label_to_type.keys())
        self.move_target_combo.configure(values=labels)
        if self.move_target_var.get() not in labels and labels:
            self.move_target_var.set(labels[0])

    def _tab_label_with_count(self, category: dict[str, object]) -> str:
        category_name = str(category["name"])
        display_name = str(category["display_name"])
        count = self._category_counts_by_type.get(category_name, 0)
        return f"{display_name} ({count})"

    def _refresh_tab_counts(self) -> None:
        self._category_counts_by_type = self.email_repo.get_new_email_counts_by_type()
        for tab_index, category in enumerate(self._categories):
            self.notebook.tab(tab_index, text=self._tab_label_with_count(category))

    def _rebuild_tabs(self) -> None:
        labels = [item["display_name"] for item in self._categories]
        current = self._current_tab if self._current_tab in labels else (labels[0] if labels else "")
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        for category in self._categories:
            self.notebook.add(ttk.Frame(self.notebook), text=self._tab_label_with_count(category))
        if current and labels:
            tab_index = labels.index(current)
            self.notebook.select(tab_index)
        self._current_tab = current

    def _current_category(self) -> dict[str, object] | None:
        selected = self._current_tab
        return next((item for item in self._categories if item["display_name"] == selected), None)

    def _open_tab_context_menu(self, event: tk.Event) -> None:
        try:
            tab_index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self.notebook.select(tab_index)
        if tab_index < len(self._categories):
            self._current_tab = str(self._categories[tab_index]["display_name"])
        category = self._current_category()
        if not category or bool(category["is_base"]):
            return
        self.tab_menu.tk_popup(event.x_root, event.y_root)

    def _create_category(self) -> None:
        display_name = simpledialog.askstring("Nueva categoría", "Nombre visible de la categoría:", parent=self)
        if display_name is None:
            return
        try:
            created = self.category_manager.create_category(display_name)
            self._reload_category_maps()
            self._current_tab = str(created["display_name"])
            self._rebuild_tabs()
            self._retrain_model()
            self.refresh_emails()
        except ValueError as exc:
            messagebox.showwarning("Atención", str(exc))

    def _rename_current_category(self) -> None:
        category = self._current_category()
        if not category:
            return
        new_display_name = simpledialog.askstring(
            "Renombrar categoría",
            "Nuevo nombre visible:",
            initialvalue=str(category["display_name"]),
            parent=self,
        )
        if new_display_name is None:
            return
        try:
            updated = self.category_manager.rename_category(str(category["name"]), new_display_name)
            self._reload_category_maps()
            self._current_tab = str(updated["display_name"])
            self._rebuild_tabs()
            self._retrain_model()
            self.refresh_emails()
        except ValueError as exc:
            messagebox.showwarning("Atención", str(exc))

    def _delete_current_category(self) -> None:
        category = self._current_category()
        if not category:
            return
        if not messagebox.askyesno(
            "Confirmación",
            "Esto eliminará su información de entrenamiento. ¿Continuar?",
        ):
            return
        try:
            self.category_manager.delete_category(str(category["name"]))
            self._reload_category_maps()
            if self._categories:
                self._current_tab = str(self._categories[0]["display_name"])
            self._rebuild_tabs()
            self._retrain_model()
            self.refresh_emails()
        except ValueError as exc:
            messagebox.showwarning("Atención", str(exc))

    def _on_tab_changed(self, _event: tk.Event) -> None:
        selected_index = self.notebook.index(self.notebook.select())
        if selected_index < len(self._categories):
            self._current_tab = str(self._categories[selected_index]["display_name"])
        self.refresh_emails()

    def _clear_filters(self) -> None:
        self.excel_filter.clear_all_filters()
        self._refresh_preview()

    def _download_new_emails(self) -> None:
        self.system_log("Iniciando descarga de emails")
        try:
            processed_ids = self.mail_ingestion_service.sync_unread_emails()
            self.system_log(f"Descarga completada. Nuevos correos: {len(processed_ids)}")
            self.status_var.set(f"Descarga completada. Nuevos correos: {len(processed_ids)}")
            self.refresh_emails()
            messagebox.showinfo("Emails", f"Se descargaron {len(processed_ids)} correos nuevos.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error descargando correos")
            self.system_log(f"Error al descargar correos: {exc}", level="ERROR")
            self.status_var.set(f"Error al descargar correos: {exc}")
            messagebox.showerror("Error", f"No se pudieron descargar correos.\n\n{exc}")

    def _recompute_senders(self) -> None:
        count = self.mail_ingestion_service.recompute_original_senders()
        messagebox.showinfo("Remitentes", f"Remitentes recalculados: {count}")
        self.refresh_emails()

    def _retrain_model(self) -> None:
        logger.info("Inicio de reentrenamiento manual del clasificador de emails")
        self.log("Iniciando reentrenamiento manual del clasificador...")
        try:
            result = self.retraining_service.check_and_retrain_dataset("email_classification", auto=False, classifier=self.classifier)
            trained = bool(result.get("trained"))
            reason = str(result.get("reason", ""))
            self.model_var.set(self.classifier.model_status())
            if trained:
                self.log(f"Entrenamiento OK: {reason}")
                logger.info("Reentrenamiento manual completado")
                return

            self.log(f"Reentrenamiento no completado: {reason}", level="WARNING")
            logger.warning("Reentrenamiento manual cancelado: %s", reason)
            messagebox.showwarning("Entrenamiento", reason or "No se pudo reentrenar el clasificador.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al intentar reentrenar el clasificador")
            self.log(f"Error de reentrenamiento: {exc}", level="ERROR")
            messagebox.showerror("Entrenamiento", f"Error al reentrenar el clasificador.\n\n{exc}")

    def _reclassify_current_emails(self) -> None:
        if not self.classifier.ml_model.is_trained:
            self.system_log("No hay modelo entrenado para reclasificar.", level="WARNING")
            self.status_var.set("No hay modelo entrenado para reclasificar.")
            return
        before = self.email_repo.get_type_distribution()
        self.system_log(f"Iniciando reclasificación de emails. Distribución antes: {before}")
        reclassified = self.classifier.reclassify_all_emails()
        after = self.email_repo.get_type_distribution()
        self.refresh_emails()
        self.system_log(f"Reclasificación completada. Correos actualizados: {reclassified}. Distribución después: {after}")
        self.status_var.set(f"Reclasificación completada. Correos actualizados: {reclassified}.")

    def refresh_emails(self) -> None:
        try:
            rows = self.email_repo.get_emails_by_types(self._tab_to_types.get(self._current_tab, ["priority"]))
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudieron cargar correos")
            self.status_var.set(f"Error al cargar correos: {exc}")
            messagebox.showerror("Error", f"No se pudieron cargar correos.\n\n{exc}")
            return

        self._all_rows = []
        self._rows_by_id = {}
        for row in rows:
            normalized = {
                "gmail_id": row["gmail_id"],
                "subject": row["subject"] or "",
                "sender": row["sender"] or "",
                "real_sender": row["original_from"] or row["real_sender"] or row["sender"] or "",
                "type": row["type"] or "other",
                "received_at": row["received_at"] or "",
                "received_at_display": self._format_datetime(row["received_at"]),
                "body_text": row["body_text"] or "",
                "body_html": row["body_html"] or "",
                "status": row["status"] or "",
                "category": row["category"] or "pending",
                "original_from": row["original_from"] or row["sender"] or "",
                "original_to": row["original_to"] or "",
                "original_cc": row["original_cc"] or "",
                "original_reply_to": row["original_reply_to"] or "",
                "reply_to": row["original_reply_to"] or "",
                "attachments_json": row["attachments_json"] or "[]",
                "entities_json": row["entities_json"] or "",
            }
            self._all_rows.append(normalized)
            self._rows_by_id[str(row["gmail_id"])] = normalized

        self.excel_filter.apply()
        self._refresh_tab_counts()
        self.classifier.examples_count = self.email_repo.count_labeled_examples()
        self.model_var.set(self.classifier.model_status())
        self.status_var.set(f"Correos cargados ({self._current_tab}): {len(rows)}")
        self._refresh_preview()

    def _set_filtered_rows(self, rows: list[dict[str, str]]) -> None:
        selected_ids = set(self.tree.selection())
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for row in rows:
            status = row["status"]
            subject = row["subject"]
            tags: tuple[str, ...] = ()

            if status == "new":
                subject = f"● {subject}"
                tags = ("email_new",)
            elif status == "ignored":
                subject = f"× {subject}"
                tags = ("email_ignored",)
            elif status == "converted_to_note":
                subject = f"✓ {subject}"
                tags = ("email_converted",)
            elif status == "forwarded":
                subject = f"→ {subject}"
                tags = ("email_forwarded",)

            values = (
                row["gmail_id"],
                subject,
                row["real_sender"],
                row["type"],
                row["received_at_display"],
                status,
            )
            iid = str(row["gmail_id"])
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)
            if iid in selected_ids:
                self.tree.selection_add(iid)

    def select_email_by_gmail_id(self, gmail_id: str) -> bool:
        target_id = str(gmail_id or "").strip()
        if not target_id:
            messagebox.showwarning("Email no encontrado", "No se encontró el correo original asociado.")
            return False

        row = self.email_repo.get_email_content(target_id)
        if row is None:
            messagebox.showwarning("Email no encontrado", "No se encontró el correo original asociado.")
            return False

        if target_id not in self._rows_by_id:
            email_type = row["type"] or "other"
            tab_label = next((label for label, types in self._tab_to_types.items() if email_type in types), self._current_tab)
            if tab_label != self._current_tab:
                self._set_tab_by_label(tab_label)
            self.refresh_emails()

        if target_id not in self._rows_by_id:
            messagebox.showwarning("Email no encontrado", "No se encontró el correo original asociado.")
            return False

        self.tree.selection_set((target_id,))
        self.tree.focus(target_id)
        self.tree.see(target_id)
        self._refresh_preview()
        return True

    def set_reply_body(self, body: str, note_id: int | None = None) -> None:
        self.set_response_draft(body, note_id)

    def get_email_attachments(self, gmail_id: str) -> list[dict[str, str]]:
        row = self.email_repo.get_email_content(str(gmail_id or "").strip())
        if row is None:
            return []
        row_data = dict(row)
        raw = row_data.get("attachments_json", "[]") or "[]"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def get_email_metadata(self, gmail_id: str) -> dict[str, str]:
        row = self.email_repo.get_email_content(str(gmail_id or "").strip())
        if row is None:
            return {}
        row_data = dict(row)
        return {
            "gmail_id": str(row_data.get("gmail_id", "") or "").strip(),
            "thread_id": str(row_data.get("thread_id", "") or "").strip(),
            "sender": str(
                row_data.get("original_from", "")
                or row_data.get("real_sender", "")
                or row_data.get("sender", "")
                or ""
            ).strip(),
            "subject": str(row_data.get("subject", "") or "").strip(),
        }

    def open_attachment(self, gmail_id: str, attachment: dict[str, str]) -> bool:
        try:
            local_path = self.attachment_cache.ensure_downloaded(str(gmail_id), attachment)
            if os.name != "nt" or not hasattr(os, "startfile"):
                raise RuntimeError("Abrir adjuntos solo está soportado en Windows (os.startfile).")
            os.startfile(local_path)  # type: ignore[attr-defined]
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir adjunto")
            self.log(f"No se pudo abrir adjunto: {exc}", level="ERROR")
            messagebox.showerror("Adjunto", f"No se pudo abrir el adjunto.\n\n{exc}")
            return False

    def set_response_draft(self, body: str, note_id: int | None = None) -> None:
        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", body or "")

        selection = self.tree.selection()
        if len(selection) == 1 and note_id is not None:
            self._pending_note_id_by_gmail_id[str(selection[0])] = note_id

    def _move_selected_emails(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para mover.")
            return

        target_label = self.move_target_var.get()
        target_type = self._move_label_to_type.get(target_label, "other")
        self.email_repo.bulk_update_type(selected_ids, target_type)
        self.email_repo.save_labels_for_emails(selected_ids, target_type, source="user")
        for gmail_id in selected_ids:
            row = self.email_repo.get_email_content(gmail_id)
            if row:
                self.email_repo.register_sender_rule(row["sender"], target_type)
                subject = str(row["subject"] or "")
                body_text = str(row["body_text"] or "")
                learn_result = self.continuous_learning_service.on_new_training_example(
                    dataset="email_classification",
                    input_text=f"{subject}\n{body_text}".strip(),
                    output_text=None,
                    label=target_type,
                    source="user_category_change",
                )
                self._save_jsonl_training_example_async(
                    dataset="email_classification",
                    input_text=f"{subject}\n{body_text}".strip(),
                    output_text=target_type,
                    metadata={
                        "email_id": str(row["gmail_id"]) if "gmail_id" in row.keys() else "",
                        "event": "user_category_change",
                    },
                )
                if not bool(learn_result.get("inserted")):
                    self.log("Ejemplo duplicado ignorado en email_classification", level="INFO")
                elif bool(learn_result.get("incremental", {}).get("trained")):
                    self.log("Incremental training aplicado correctamente", level="INFO")
                else:
                    self.log(
                        f"Incremental training omitido: {learn_result.get('incremental', {}).get('reason', 'pendiente full retrain')}",
                        level="INFO",
                    )

        self.status_var.set(f"{len(selected_ids)} correos movidos a {target_label}.")
        self.refresh_emails()

    def _create_notes_from_selected_emails(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para crear notas.")
            return
        self.system_log(f"Iniciando creación de notas para {len(selected_ids)} emails")

        response_text = self.response_text.get("1.0", "end").strip()
        editor_text = response_text
        summary_text = self._extract_quick_summary(response_text)
        include_summary = False
        if summary_text:
            include_summary = messagebox.askyesno(
                "Integrar resumen",
                "Se ha detectado un 'Resumen rápido'. ¿Deseas integrarlo al principio de la nota?",
            )

        created_count = 0
        skipped_count = 0
        for gmail_id in selected_ids:
            row = self.email_repo.get_email_content(gmail_id)
            if row is None:
                self.system_log(f"Email no encontrado para crear nota: {gmail_id}", level="WARNING")
                skipped_count += 1
                continue
            if (row["status"] or "") == "converted_to_note":
                skipped_count += 1
                continue

            subject = (row["subject"] or "").strip()
            body_text = (row["body_text"] or "").strip()
            body_html = (row["body_html"] or "").strip()
            if not subject and not body_text and not body_html:
                self.system_log(f"Email sin contenido para crear nota: {gmail_id}", level="WARNING")
                skipped_count += 1
                continue

            try:
                prepared_context = self._get_prepared_context_for_gmail_id(gmail_id)
                if prepared_context is None:
                    prepared_context = self._prompt_prepare_context_if_missing(gmail_id, row)
                merged_content = (prepared_context or {}).get("merged_content", "").strip()

                title = self._prompt_note_title(subject)
                if title is None:
                    skipped_count += 1
                    continue

                req = self._build_note_request_from_row(
                    row,
                    title,
                    summary_text=summary_text,
                    include_summary=include_summary,
                    prepared_merged_content=merged_content,
                )
                self.system_log(f"Analizando nota para email {gmail_id}")
                note_id, _message = self.note_service.create_note(req)
                if note_id is None:
                    self.system_log(f"No se creó la nota para email {gmail_id}", level="WARNING")
                    skipped_count += 1
                    continue
                tasks_count = self.note_service.actions_repo.pending_count_by_note(note_id)
                self.system_log(f"Nota creada OK gmail_id={gmail_id} notion_id={note_id}")
                self.system_log(f"Tareas detectadas: {tasks_count}")
                self.system_log(f"Tareas creadas: {tasks_count}")
                self.email_repo.update_status(gmail_id, "converted_to_note")
                if merged_content:
                    self.system_log("Creando nota desde contexto preparado")
                else:
                    self.system_log("No existe contexto preparado; usando flujo actual")
                if include_summary:
                    self.log("Resumen integrado en la nota creada desde email")
                created_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("No se pudo crear nota desde email %s", gmail_id)
                self.system_log(f"Error al crear nota desde email {gmail_id}: {exc}", level="ERROR")
                messagebox.showerror("Crear nota", f"Error al crear nota desde {gmail_id}.\n\n{exc}")
                skipped_count += 1

        self.refresh_emails()
        if created_count and callable(self.calendar_refresh_callback):
            self.calendar_refresh_callback()
        self.system_log(f"Creación de notas finalizada. Notas: {created_count}, omitidos: {skipped_count}")
        messagebox.showinfo("Resultado", f"Notas creadas: {created_count}\nOmitidos: {skipped_count}")

    def _create_events_from_selected_emails(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para crear eventos.")
            return

        response_text = self.response_text.get("1.0", "end").strip()
        editor_text = response_text
        summary_text = self._extract_quick_summary(response_text)
        include_summary = False
        if summary_text:
            include_summary = messagebox.askyesno(
                "Integrar resumen",
                "¿Deseas incluir el resumen en el evento?",
            )

        created_count = 0
        skipped_count = 0
        for gmail_id in selected_ids:
            row = self.email_repo.get_email_content(gmail_id)
            if row is None:
                skipped_count += 1
                continue
            if (row["status"] or "") == "converted_to_event":
                skipped_count += 1
                continue

            prepared_context = self._get_prepared_context_for_gmail_id(gmail_id)
            if prepared_context is None:
                prepared_context = self._prompt_prepare_context_if_missing(gmail_id, row)
            merged_content = (prepared_context or {}).get("merged_content", "").strip()
            email_original_text = self._get_email_original_text(row).strip()

            payload = self._prompt_event_creation_data(row)
            if payload is None:
                skipped_count += 1
                continue

            event_body = ""
            if merged_content:
                event_body = merged_content
            elif editor_text:
                event_body = editor_text
            elif email_original_text:
                event_body = email_original_text

            if not event_body:
                skipped_count += 1
                self.system_log(
                    f"No se pudo crear evento para {gmail_id}: contenido vacío en contexto, editor y email original",
                    level="WARNING",
                )
                continue

            event_request = NoteCreateRequest(
                title=payload["title"],
                raw_text=event_body,
                source="email_pasted",
                area=self._resolve_default_value("Area", "default_area", "General"),
                tipo="Evento",
                estado=self._resolve_default_value("Estado", "default_estado", "Pendiente"),
                prioridad=self._resolve_default_value("Prioridad", "default_prioridad", "Media"),
                fecha=payload["date"],
                hora_inicio=payload["time_start"],
                hora_fin=payload["time_end"],
                email_id=str(row["gmail_id"] or "").strip(),
                google_calendar_id=payload["calendar_id"],
            )

            note_id, _message = self.note_service.create_note(event_request)
            if note_id is None:
                skipped_count += 1
                continue

            self.email_repo.update_status(gmail_id, "converted_to_event")
            logger.info(f"Email {gmail_id} converted to event")

            self.log("Evento creado desde email")
            if merged_content:
                self.system_log("Creando evento desde contexto preparado")
            else:
                self.system_log("No existe contexto preparado; usando flujo actual")
            if include_summary and summary_text:
                self.log("Resumen rápido integrado en evento")
            created_count += 1

        self.refresh_emails()
        if created_count and callable(self.calendar_refresh_callback):
            self.calendar_refresh_callback()
        messagebox.showinfo("Resultado", f"Eventos creados: {created_count}\nOmitidos: {skipped_count}")

    def _prompt_event_creation_data(self, row: sqlite3.Row) -> dict[str, str] | None:
        dialog = tk.Toplevel(self)
        dialog.title("Crear evento")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        suggested_date = self._suggest_event_date_from_email(row)
        default_title = (row["subject"] or "").strip() or "(Sin título)"
        ttk.Label(dialog, text="Título").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        title_var = tk.StringVar(value=default_title)
        title_entry = ttk.Entry(dialog, textvariable=title_var, width=48)
        title_entry.grid(row=0, column=1, padx=8, pady=6, sticky="ew")

        ttk.Label(dialog, text="Fecha").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        date_picker = DateEntry(dialog, date_pattern="yyyy-mm-dd", state="readonly")
        date_picker.set_date(suggested_date)
        date_picker.grid(row=1, column=1, padx=8, pady=6, sticky="w")

        time_values = self._generate_time_values()
        ttk.Label(dialog, text="Hora inicio").grid(row=2, column=0, padx=8, pady=6, sticky="w")
        time_start = ttk.Combobox(dialog, values=time_values, state="readonly", width=8)
        time_start.set("09:00")
        time_start.grid(row=2, column=1, padx=8, pady=6, sticky="w")

        ttk.Label(dialog, text="Hora fin (opcional)").grid(row=3, column=0, padx=8, pady=6, sticky="w")
        time_end_values = [""] + time_values
        time_end = ttk.Combobox(dialog, values=time_end_values, state="readonly", width=8)
        time_end.set("")
        time_end.grid(row=3, column=1, padx=8, pady=6, sticky="w")

        ttk.Label(dialog, text="Calendario destino").grid(row=4, column=0, padx=8, pady=6, sticky="w")
        calendars = self.calendar_repo.list_calendars()
        calendar_names = [str(item["name"]) for item in calendars]
        id_by_name = {str(item["name"]): str(item["google_calendar_id"]) for item in calendars}
        calendar_combo = ttk.Combobox(dialog, values=calendar_names, state="readonly", width=34)
        primary = self.calendar_repo.get_primary_calendar()
        if primary is not None:
            calendar_combo.set(str(primary["name"]))
        elif calendar_names:
            calendar_combo.set(calendar_names[0])
        calendar_combo.grid(row=4, column=1, padx=8, pady=6, sticky="w")

        result: dict[str, str] = {}

        def _accept() -> None:
            cleaned_title = title_var.get().strip()
            if not cleaned_title:
                messagebox.showwarning("Crear evento", "El título no puede estar vacío.", parent=dialog)
                return
            calendar_name = calendar_combo.get().strip()
            calendar_id = id_by_name.get(calendar_name, "")
            result.update(
                {
                    "title": cleaned_title,
                    "date": date_picker.get_date().strftime("%Y-%m-%d"),
                    "time_start": time_start.get().strip(),
                    "time_end": time_end.get().strip(),
                    "calendar_id": calendar_id,
                }
            )
            dialog.destroy()

        def _cancel() -> None:
            dialog.destroy()

        dialog.columnconfigure(1, weight=1)
        buttons = ttk.Frame(dialog)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", padx=8, pady=(4, 10))
        ttk.Button(buttons, text="Cancelar", command=_cancel).pack(side="right", padx=(4, 0))
        ttk.Button(buttons, text="Crear", command=_accept).pack(side="right")

        title_entry.focus_set()
        self.wait_window(dialog)
        return result or None

    def _compose_event_body_from_email(
        self,
        row: sqlite3.Row,
        summary_text: str,
        include_summary: bool,
        merged_content: str = "",
    ) -> str:
        if merged_content.strip():
            return self._build_event_body_from_prepared_context(merged_content)
        email_text = self._get_email_text_for_note(row)
        if include_summary and summary_text:
            return (
                "RESUMEN RÁPIDO\n"
                "--------------\n"
                f"{summary_text.strip()}\n\n"
                "EMAIL ORIGINAL\n"
                "--------------\n"
                f"{email_text.strip()}"
            ).strip()
        return (
            f"ASUNTO DEL EMAIL\n--------------\n{(row['subject'] or '').strip()}\n\n"
            f"EMAIL ORIGINAL\n--------------\n{email_text.strip()}"
        ).strip()

    @staticmethod
    def _build_event_body_from_prepared_context(merged_content: str) -> str:
        text = (merged_content or "").strip()
        if not text:
            return ""

        max_chars = 3500
        if len(text) <= max_chars:
            return text

        blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
        compact_sections: list[str] = []
        original_block = ""
        for block in blocks:
            if block.upper().startswith("EMAIL ORIGINAL"):
                original_block = block
                continue
            compact_sections.append(block)

        compact = "\n\n".join(compact_sections).strip()
        remaining = max_chars - len(compact) - 2
        if remaining > 80 and original_block:
            compact_original = original_block[:remaining].rstrip()
            if len(compact_original) < len(original_block):
                compact_original = f"{compact_original}\n[...]"
            compact = f"{compact}\n\n{compact_original}".strip()
        return compact[:max_chars].strip()

    def _suggest_event_date_from_email(self, row: sqlite3.Row) -> datetime.date:
        email_text = "\n".join(
            [
                str(row.get("subject", "") if hasattr(row, "get") else row["subject"] or ""),
                str(row.get("body_text", "") if hasattr(row, "get") else row["body_text"] or ""),
            ]
        )
        detected = self._extract_date_from_text(email_text)
        if detected is not None:
            return detected
        received = self._safe_parse_date(self._resolve_note_date(row["received_at"]))
        return received or datetime.now().date()

    @staticmethod
    def _extract_date_from_text(text: str) -> datetime.date | None:
        normalized = (text or "").strip()
        if not normalized:
            return None

        patterns = [
            r"\b(\d{4})-(\d{2})-(\d{2})\b",
            r"\b(\d{2})/(\d{2})/(\d{4})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            try:
                if pattern.startswith(r"\b(\d{4})"):
                    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                else:
                    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return datetime(year, month, day).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _safe_parse_date(value: str) -> datetime.date | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _generate_time_values() -> list[str]:
        values: list[str] = []
        for hour in range(24):
            for minute in (0, 15, 30, 45):
                values.append(f"{hour:02d}:{minute:02d}")
        return values

    def _prompt_note_title(self, default_title: str) -> str | None:
        title = simpledialog.askstring(
            "Crear nota",
            "Título de la nota:",
            initialvalue=default_title,
            parent=self,
        )
        if title is None:
            return None

        cleaned = title.strip()
        if not cleaned:
            messagebox.showwarning("Crear nota", "El título no puede estar vacío.")
            return None
        return cleaned

    def _build_note_request_from_row(
        self,
        row: sqlite3.Row,
        title: str | None = None,
        summary_text: str | None = None,
        include_summary: bool = False,
        prepared_merged_content: str = "",
    ) -> NoteCreateRequest:
        sender_for_note = row["original_from"] or row["real_sender"] or row["sender"] or ""
        note_title = (title if title is not None else (row["subject"] or "")).strip()
        email_text = prepared_merged_content.strip() or self._get_email_text_for_note(row)
        if not prepared_merged_content.strip() and include_summary and summary_text:
            email_text = self._compose_note_body_with_summary(email_text, summary_text)
        return NoteCreateRequest(
            title=note_title,
            raw_text=self._compose_note_text(
                note_title,
                sender_for_note,
                email_text,
                (row["body_html"] or "").strip(),
            ),
            source="email_pasted",
            area=self._resolve_default_value("Area", "default_area", "General"),
            tipo=self._resolve_default_value("Tipo", "default_tipo", "Nota"),
            estado=self._resolve_default_value("Estado", "default_estado", "Pendiente"),
            prioridad=self._resolve_default_value("Prioridad", "default_prioridad", "Media"),
            fecha=self._resolve_note_date(row["received_at"]),
            email_id=str(row["gmail_id"] or "").strip(),
        )

    def _delete_selected_emails(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para eliminar.")
            return

        confirmed = messagebox.askyesno(
            "Confirmación",
            f"¿Eliminar {len(selected_ids)} correos de la base local?\n(No se borrarán de Gmail)",
        )
        if not confirmed:
            return

        self.email_repo.delete_emails(selected_ids)
        self.refresh_emails()

    def _mark_selected_as_ignored(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para marcar como ignorado.")
            return

        self.email_repo.bulk_update_status(selected_ids, "ignored")
        self.refresh_emails()

    def _show_tree_context_menu(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        if row_id:
            selected = self.tree.selection()
            if row_id not in selected:
                self.tree.selection_set(row_id)
                self._refresh_preview()

        if self.tree_context_menu is None:
            return

        try:
            self.tree_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.tree_context_menu.grab_release()

    def _selected_ids(self) -> list[str]:
        return [str(iid) for iid in self.tree.selection()]

    def _select_all_rows(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)
            self._refresh_preview()

    def _select_rows_by_type(self, email_type: str) -> None:
        to_select = [iid for iid in self.tree.get_children() if (self._rows_by_id.get(str(iid), {}).get("type") == email_type)]
        self.tree.selection_set(to_select)
        self._refresh_preview()

    def _select_unread_rows(self) -> None:
        to_select = [iid for iid in self.tree.get_children() if (self._rows_by_id.get(str(iid), {}).get("status") == "new")]
        self.tree.selection_set(to_select)
        self._refresh_preview()

    def _clear_selection(self) -> None:
        self.tree.selection_remove(self.tree.selection())
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        selection = self.tree.selection()
        attachments: list[dict[str, str]] = []
        body_html = ""
        body_text = ""
        detected_entities: dict[str, str] = {}
        if len(selection) == 1:
            row = self._rows_by_id.get(str(selection[0]))
            if row:
                body_html = row.get("body_html", "").strip()
                body_text = row.get("body_text", "")
                attachments = self._build_email_attachments(str(row["gmail_id"]))
                detected_entities = self._resolve_entities(row)

        self._set_html_preview(body_html, body_text)
        self._render_attachments(attachments)
        self._set_detected_entities(detected_entities)


    def _resolve_entities(self, row: dict[str, str]) -> dict[str, str]:
        entities_raw = str(row.get("entities_json", "") or "").strip()
        if entities_raw:
            try:
                parsed = json.loads(entities_raw)
                if isinstance(parsed, dict):
                    return {
                        "pedido": str(parsed.get("pedido", "") or ""),
                        "cliente": str(parsed.get("cliente", "") or ""),
                        "producto": str(parsed.get("producto", "") or ""),
                        "persona": str(parsed.get("persona", "") or ""),
                        "email_persona": str(parsed.get("email_persona", "") or ""),
                        "accion": str(parsed.get("accion", "") or ""),
                    }
            except json.JSONDecodeError:
                logger.warning("entities_json inválido para email %s", row.get("gmail_id", ""))
        return EmailEntityExtractor.extract_entities(row.get("subject", ""), row.get("body_text", ""))

    def _set_detected_entities(self, entities: dict[str, str]) -> None:
        self.detected_pedido_var.set(str(entities.get("pedido", "") or ""))
        self.detected_cliente_var.set(str(entities.get("cliente", "") or ""))
        self.detected_persona_var.set(str(entities.get("persona", "") or ""))
        self.detected_accion_var.set(str(entities.get("accion", "") or ""))

    def _set_html_preview(self, body_html: str, body_text: str = "") -> None:
        html_body = strip_outlook_word_html((body_html or "").strip())
        text_body = body_text or ""

        if is_real_html(html_body):
            self._current_html_content = html_body
            preview_content = html_body
        else:
            self._current_html_content = ""
            clean_text = clean_outlook_content(text_body)
            clean_text = html.escape(clean_text)
            preview_content = f"<pre>{clean_text}</pre>"

        preview_content = _sanitize_html_colors(preview_content)

        if self.preview_html is not None:
            self.preview_html.set_html(preview_content)

        if self._expanded_html_frame is not None:
            content = self._html_to_text(self._current_html_content) or text_body or "Sin contenido HTML."
            self._expanded_html_frame.configure(state="normal")
            self._expanded_html_frame.delete("1.0", "end")
            self._expanded_html_frame.insert("1.0", content)
            self._expanded_html_frame.configure(state="disabled")

    def _build_email_attachments(self, gmail_id: str) -> list[dict[str, str]]:
        row = self._rows_by_id.get(str(gmail_id), {})
        attachments: list[dict[str, str]] = []
        raw_json = str(row.get("attachments_json", "") or "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        attachments.append(
                            {
                                "filename": str(item.get("filename") or ""),
                                "mime": str(item.get("mimeType") or "application/octet-stream"),
                                "attachmentId": str(item.get("attachmentId") or ""),
                                "partId": str(item.get("partId") or ""),
                                "size": str(item.get("size") or 0),
                                "local_path": "",
                            }
                        )
            except json.JSONDecodeError:
                self.log(f"attachments_json inválido para {gmail_id}", level="WARNING")

        by_name = {item.get("filename", ""): item for item in attachments}
        for attachment in self.email_repo.get_attachments(gmail_id):
            name = str(attachment["filename"] or "")
            current = by_name.get(name)
            if current is None:
                current = {"filename": name, "mime": str(attachment["mime_type"] or "application/octet-stream")}
                attachments.append(current)
                by_name[name] = current
            current["local_path"] = str(attachment["local_path"] or "")
        return attachments

    def _render_attachments(self, attachments: list[dict[str, str]]) -> None:
        self._current_attachments = attachments
        self.attachments_list.delete(0, "end")
        if not attachments:
            self.attachments_list.insert("end", "(sin adjuntos)")
            return
        for attachment in attachments:
            filename = attachment.get("filename", "(sin nombre)")
            mime = attachment.get("mime", "")
            size = str(attachment.get("size", "") or "").strip()
            suffix = f" [{mime}]"
            if size and size != "0":
                suffix += f" ({size} bytes)"
            self.attachments_list.insert("end", f"{filename}{suffix}")

    def _selected_attachment(self) -> dict[str, str] | None:
        if not hasattr(self, "_current_attachments"):
            return None
        idx = self.attachments_list.curselection()
        if not idx:
            return None
        if not self._current_attachments:
            return None
        pos = int(idx[0])
        if pos >= len(self._current_attachments):
            return None
        return self._current_attachments[pos]

    def _download_selected_attachment(self) -> None:
        attachment = self._selected_attachment()
        if not attachment:
            messagebox.showwarning("Adjuntos", "Selecciona un adjunto.")
            return
        row_id = self.tree.selection()
        if len(row_id) != 1:
            messagebox.showwarning("Adjuntos", "Selecciona un email.")
            return
        gmail_id = str(row_id[0])
        try:
            local_path = self.attachment_cache.ensure_downloaded(gmail_id, attachment)
            attachment["local_path"] = local_path
            self.log(f"Adjunto descargado en: {local_path}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error descargando adjunto")
            self.log(f"Error al descargar adjunto: {exc}", level="ERROR")
            messagebox.showerror("Adjuntos", str(exc))

    def _open_selected_attachment(self) -> None:
        attachment = self._selected_attachment()
        if not attachment:
            messagebox.showwarning("Adjuntos", "Selecciona un adjunto.")
            return
        self._open_attachment(attachment)

    def _save_selected_attachment(self) -> None:
        attachment = self._selected_attachment()
        if not attachment:
            messagebox.showwarning("Adjuntos", "Selecciona un adjunto.")
            return
        local_path = attachment.get("local_path", "")
        if not local_path:
            self._download_selected_attachment()
            local_path = attachment.get("local_path", "")
        if not local_path:
            return
        self._save_attachment_as(local_path, attachment.get("filename", "adjunto"))

    def _attach_selected_to_draft(self) -> None:
        attachment = self._selected_attachment()
        if not attachment:
            messagebox.showwarning("Adjuntos", "Selecciona un adjunto.")
            return
        self._download_selected_attachment()
        messagebox.showinfo("Adjuntos", "El adjunto se incluirá si creas borrador/reenvío con adjuntos.")
    def _open_attachment(self, attachment: dict[str, str]) -> None:
        local_path = attachment.get("local_path", "")
        path = Path(local_path)
        if not path.exists():
            self.log(f"Adjunto no encontrado para abrir: {local_path}", level="ERROR")
            messagebox.showerror("Adjunto", f"No existe el archivo:\n{local_path}")
            return

        try:
            suffix = path.suffix or Path(attachment.get("filename", "")).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(path.read_bytes())
            temp_path = Path(temp_file.name)
            self._temp_opened_attachments.append(temp_path)
            if hasattr(os, "startfile"):
                os.startfile(str(temp_path))  # type: ignore[attr-defined]
                self.log(f"Adjunto abierto: {temp_path}")
            else:
                raise RuntimeError("Abrir adjuntos solo está soportado en Windows (os.startfile).")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir adjunto")
            self.log(f"No se pudo abrir adjunto: {exc}", level="ERROR")
            messagebox.showerror("Adjunto", f"No se pudo abrir el adjunto.\n\n{exc}")

    def _save_attachment_as(self, local_path: str, filename: str) -> None:
        source = Path(local_path)
        if not source.exists():
            self.log(f"Adjunto no encontrado para guardar: {local_path}", level="ERROR")
            messagebox.showerror("Adjunto", f"No existe el archivo:\n{local_path}")
            return

        target = filedialog.asksaveasfilename(parent=self, initialfile=filename, title="Guardar adjunto como")
        if not target:
            return

        try:
            shutil.copy2(source, Path(target))
            self.log(f"Adjunto guardado en: {target}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo guardar adjunto")
            messagebox.showerror("Adjunto", f"No se pudo guardar el adjunto.\n\n{exc}")

    @staticmethod
    def _is_image_attachment(filename: str, mime_type: str) -> bool:
        lowered_name = filename.lower()
        lowered_mime = (mime_type or "").lower()
        return lowered_mime.startswith("image/") or lowered_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))

    def _preview_attachment_image(self, local_path: str, filename: str) -> None:
        messagebox.showinfo("Adjunto", "La vista previa embebida no está disponible. Usa 'Abrir'.")

    def _close_attachment_preview(self) -> None:
        if self._expanded_attachment_preview_window is not None:
            self._expanded_attachment_preview_window.destroy()
        self._expanded_attachment_preview_window = None
        self._expanded_attachment_preview_frame = None

    def _expand_html_view(self) -> None:
        if self._expanded_html_window is None or not self._expanded_html_window.winfo_exists():
            self._expanded_html_window = tk.Toplevel(self)
            apply_app_icon(self._expanded_html_window)
            self._expanded_html_window.title("Vista HTML expandida")
            self._expanded_html_window.geometry("1100x700")
            self._expanded_html_frame = ScrolledText(self._expanded_html_window, wrap="word", state="disabled")
            self._expanded_html_frame.pack(fill="both", expand=True)
            self._expanded_html_window.protocol("WM_DELETE_WINDOW", self._close_expanded_html_view)

        if self._expanded_html_frame is not None:
            self._expanded_html_frame.configure(state="normal")
            self._expanded_html_frame.delete("1.0", "end")
            self._expanded_html_frame.insert("1.0", self._html_to_text(self._current_html_content) or "Sin contenido HTML.")
            self._expanded_html_frame.configure(state="disabled")

        self._expanded_html_window.lift()
        self._expanded_html_window.focus_force()

    def _close_expanded_html_view(self) -> None:
        if self._expanded_html_window is not None:
            self._expanded_html_window.destroy()
        self._expanded_html_window = None
        self._expanded_html_frame = None

    def _generate_response(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para generar respuesta.")
            return
        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return

        sender = str(row.get("sender", "")).lower()
        sender_type = "interno" if "sansebas.es" in sender else "externo"

        examples = self.training_repo.get_similar_examples(
            category=row["category"],
            subject=row["subject"],
            body=row["body_text"],
            sender_type=sender_type,
            limit=3,
        )

        prompt = self._build_response_prompt(row, examples)
        body = self._generate_ai_response(prompt)
        if not body:
            subject = row["subject"].strip() or "tu mensaje"
            body = (
                "Hola,\n\n"
                f"Gracias por tu correo sobre '{subject}'.\n"
                "Lo he revisado y te responderé con el detalle correspondiente a la mayor brevedad.\n\n"
                "Saludos,"
            )

        self._open_response_review_dialog(row=row, draft_ai_response=body)

    def _generate_ai_response(self, prompt: str) -> str:
        try:
            client = build_openai_client()
            response = client.responses.create(
                model=MODEL_NAME,
                input=[
                    {
                        "role": "system",
                        "content": "Redacta respuestas de email profesionales en español. Devuelve solo el texto de respuesta.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            return str(response.output_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo generar la respuesta con IA: %s", exc)
            self.status_var.set("No se pudo usar IA; se generó una respuesta base.")
            return ""

    def _build_response_prompt(self, row: dict[str, str], examples: list[dict[str, str]]) -> str:
        current_email = build_email_training_input_text(
            subject=row.get("subject", ""),
            sender=row.get("real_sender") or row.get("sender", ""),
            body_text=row.get("body_text", ""),
        )
        learned_examples_context = self.global_learning_store.build_prompt_context(
            tipo="email_reply",
            input_actual=current_email,
            max_ejemplos=EMAIL_SUMMARY_EXAMPLES_LIMIT,
        )

        examples_lines = [
            "Eres un asistente que redacta correos en el estilo del usuario.",
            "",
            "Estilo del usuario:",
            "- Saludo preferido: Buenos días,",
            "- Tono: profesional directo",
            "- Longitud: media",
            "- Evitar: Estimado",
            "",
            "Ejemplos reales del usuario:",
            "",
        ]
        for index, example in enumerate(examples, start=1):
            examples_lines.extend(
                [
                    f"Ejemplo {index}:",
                    f"Asunto: {example.get('original_subject', '').strip()}",
                    "Correo original:",
                    example.get("original_body", "").strip(),
                    "Respuesta enviada:",
                    example.get("response_text", "").strip(),
                    "",
                ]
            )
        if learned_examples_context:
            examples_lines.extend([learned_examples_context, ""])
        entities = self._resolve_entities(row)
        examples_lines.extend(
            [
                "--------------------------------------",
                "",
                "Datos detectados:",
                f"- Pedido: {entities.get('pedido', '').strip()}",
                f"- Cliente: {entities.get('cliente', '').strip()}",
                f"- Producto: {entities.get('producto', '').strip()}",
                f"- Persona: {entities.get('persona', '').strip()}",
                f"- Email persona: {entities.get('email_persona', '').strip()}",
                f"- Acción: {entities.get('accion', '').strip()}",
                "",
                "Ahora redacta la respuesta al siguiente correo:",
                "",
                f"Asunto: {row.get('subject', '').strip()}",
                "Correo:",
                f"{row.get('body_text', '').strip()}",
                "",
                "--------------------------------------",
            ]
        )
        return "\n".join(examples_lines).strip()

    def _get_fixed_style_profile(self) -> str:
        return os.getenv("EMAIL_RESPONSE_STYLE_PROFILE", "").strip()

    def _normalize_recipients(
        self,
        main_sender: str,
        original_to: str,
        original_cc: str,
        reply_to: str,
    ) -> tuple[str, str]:
        my_email = (self.my_email or "").lower().strip()

        to_list: list[str] = []
        cc_list: list[str] = []

        primary = (reply_to or main_sender or "").lower().strip()
        if primary and primary != my_email:
            to_list.append(primary)

        parsed_to = [addr for _, addr in getaddresses([original_to or ""])]
        parsed_cc = [addr for _, addr in getaddresses([original_cc or ""])]

        for email in parsed_to:
            normalized = email.lower().strip()
            if normalized and normalized != my_email and normalized not in to_list:
                to_list.append(normalized)

        for email in parsed_cc:
            normalized = email.lower().strip()
            if normalized and normalized != my_email and normalized not in to_list and normalized not in cc_list:
                cc_list.append(normalized)

        return ", ".join(to_list), ", ".join(cc_list)

    def _create_outlook_draft(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para crear borrador.")
            return

        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return

        subject = row["subject"].strip()
        draft_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        body = self.response_text.get("1.0", "end").strip()
        if not body:
            messagebox.showwarning("Atención", "Escribe o genera una respuesta antes de crear el borrador.")
            return

        main_sender = row.get("real_sender") or row.get("sender", "")
        to_recipients, cc_recipients = self._normalize_recipients(
            main_sender=main_sender,
            original_to=row.get("original_to", ""),
            original_cc=row.get("original_cc", ""),
            reply_to=row.get("reply_to", ""),
        )
        if not to_recipients:
            messagebox.showwarning("Atención", "No se encontró destinatario para responder este correo.")
            return
        reply_to = row.get("reply_to", "")

        reply_all = messagebox.askyesno("Responder", "¿Responder a todos (incluyendo CC)?")
        if not reply_all:
            cc_recipients = ""

        attachments = self._build_email_attachments(str(row["gmail_id"]))
        attachment_paths = self._resolve_reply_attachment_paths(str(row["gmail_id"]), attachments)
        if attachment_paths is None:
            return

        try:
            self.log("Creando borrador en Outlook...")
            self.log(f"Destinatarios: {to_recipients} / CC: {cc_recipients}")
            to_recipient, cc_recipients = self.outlook_service.create_draft(
                subject=draft_subject,
                body=body,
                original_from=to_recipients,
                original_to="",
                original_cc=cc_recipients,
                my_email=self.my_email,
                original_reply_to=reply_to,
                attachment_paths=attachment_paths,
            )
            self.log("Borrador creado correctamente")
            self.log(f"Responder a: {to_recipient} / CC: {', '.join(cc_recipients)}")
            for path in attachment_paths or []:
                self.log(f"Adjunto añadido a borrador: {path}")
            self.log("Borrador de Outlook abierto correctamente.")
            self.email_repo.update_status(str(row["gmail_id"]), "responded")
            self.log(f"Email {row['gmail_id']} marcado como responded")
            self.refresh_emails()

            gmail_id = str(row["gmail_id"])
            note_id = self._pending_note_id_by_gmail_id.get(gmail_id)
            if note_id is None:
                note = self.note_service.get_note_by_source("email_pasted", gmail_id)
                note_id = note.id if note else None
            if note_id is not None:
                self.note_service.note_repo.update_estado(note_id, "Responded")
                self.note_service.note_repo.set_email_replied(note_id)
                self.log(f"Nota {note_id} actualizada a Responded")

        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo crear borrador de Outlook")
            self.log(f"Error creando borrador Outlook: {exc}", level="ERROR")
            messagebox.showerror("Error", f"No se pudo crear el borrador en Outlook.\n\n{exc}")

    def _forward_email(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para reenviar.")
            return

        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return

        attachments = self._build_email_attachments(str(row["gmail_id"]))
        attachment_paths = self._resolve_reply_attachment_paths(str(row["gmail_id"]), attachments)
        if attachment_paths is None:
            return
        forward_body = (
            "---- Mensaje reenviado ----\n"
            f"De: {row.get('real_sender') or row.get('sender', '')}\n"
            f"Fecha: {self._format_datetime(row.get('received_at', ''))}\n"
            f"Para: {row.get('original_to', '')}\n"
            f"Asunto: {row.get('subject', '')}\n\n"
            f"{row.get('body_text', '')}"
        )

        try:
            self.outlook_service.create_forward_draft(
                subject=f"FW: {row.get('subject', '').strip()}",
                body=forward_body,
                attachment_paths=attachment_paths,
            )
            self.email_repo.update_status(str(row["gmail_id"]), "responded")
            self.log(f"Email {row['gmail_id']} marcado como responded")
            gmail_id = str(row["gmail_id"])
            note_id = self._pending_note_id_by_gmail_id.get(gmail_id)
            if note_id is None:
                note = self.note_service.get_note_by_source("email_pasted", gmail_id)
                note_id = note.id if note else None
            if note_id is not None:
                self.note_service.note_repo.update_estado(note_id, "Forwarded")
                self.note_service.note_repo.set_email_replied(note_id)
                self.log(f"Nota {note_id} actualizada a Forwarded")
            self.refresh_emails()
            for path in attachment_paths or []:
                self.log(f"Adjunto añadido a borrador: {path}")
            self.log("Borrador de reenvío abierto correctamente.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo crear borrador de reenvío")
            self.log(f"Error creando borrador de reenvío: {exc}", level="ERROR")
            messagebox.showerror("Error", f"No se pudo crear el borrador de reenvío en Outlook.\n\n{exc}")

    def _summarize_email(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para resumir.")
            return

        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return

        preview_body = self._html_to_text(self._current_html_content).strip() if self._current_html_content else ""
        if not preview_body:
            preview_body = self._get_email_original_text(row)
        if not preview_body:
            messagebox.showwarning("Atención", "El correo seleccionado no tiene contenido para resumir.")
            return

        # Nuevo prompt orientado a lectura ultrarrápida en viñetas, evitando formato de email formal.
        prompt = self._build_email_summary_prompt(preview_body)
        try:
            self.log("Generando resumen...")
            client = build_openai_client()
            response = client.responses.create(
                model="gpt-4.1-mini",
                input=prompt,
            )
            summary = str(response.output_text or "").strip()
            if not summary:
                messagebox.showwarning("Atención", "No se pudo generar el resumen del email.")
                return
            self._open_summary_review_dialog(row=row, ai_summary=summary, preview_body=preview_body)
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo generar el resumen")
            self.log(f"Error generando resumen: {exc}", level="ERROR")
            messagebox.showerror("OpenAI", f"No se pudo generar el resumen.\n\n{exc}")

    def _build_email_summary_prompt(self, preview_body: str) -> str:
        examples_context = self.global_learning_store.build_prompt_context(
            tipo="email_summary",
            input_actual=preview_body,
            max_ejemplos=EMAIL_SUMMARY_EXAMPLES_LIMIT,
        )
        sections = [
            "Analiza el siguiente email y extrae únicamente las ideas principales.\n\n"
            "Devuelve un resumen visual para lectura rápida.\n\n"
            "Reglas:\n"
            "- máximo 6 líneas\n"
            "- cada línea una idea independiente\n"
            "- usar viñetas (•)\n"
            "- frases muy cortas\n"
            "- no incluir saludos ni despedidas\n"
            "- no copiar frases completas del email\n"
            "- lenguaje claro y directo\n\n"
            "El objetivo es que el contenido del email se entienda en menos de 5 segundos."
        ]
        if examples_context:
            sections.append(examples_context)
        sections.append(f"Email:\n{preview_body}")
        return "\n\n".join(sections)

    def _summarize_attachments(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para resumir adjuntos.")
            return

        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return

        gmail_id = str(row.get("gmail_id", "")).strip()
        if not gmail_id:
            return

        attachments = self._build_email_attachments(gmail_id)
        logger.info("Attachments detected: %s", attachments)
        useful_attachments = [item for item in attachments if self._is_summarizable_attachment(item)]
        if not useful_attachments:
            messagebox.showinfo("Adjuntos", "No se encontró contenido resumible en los adjuntos")
            self.log("No hay adjuntos útiles para resumir")
            return

        prepared_attachments: list[dict[str, str]] = []
        attachment_types: list[str] = []
        for attachment in useful_attachments:
            logger.info("Attachment candidate: %s", attachment)
            filename = self._extract_attachment_filename(str(attachment.get("filename") or "adjunto")) or "adjunto"
            try:
                local_path = self.attachment_cache.ensure_downloaded(gmail_id, attachment)
                attachment["local_path"] = local_path
                prepared_attachments.append(
                    {
                        "file_path": local_path,
                        "local_path": local_path,
                        "filename": filename,
                        "mime_type": str(attachment.get("mime") or attachment.get("mime_type") or ""),
                    }
                )
                suffix = (Path(filename).suffix.lower() or Path(local_path).suffix.lower()).lstrip(".")
                if suffix and suffix not in attachment_types:
                    attachment_types.append(suffix)
            except Exception as exc:  # noqa: BLE001
                self.log(f"No se pudo leer adjunto {filename}: {exc}", level="WARNING")

        if not prepared_attachments:
            messagebox.showinfo("Adjuntos", "No se encontró contenido resumible en los adjuntos")
            self.log("No se pudieron preparar adjuntos resumibles")
            return

        extracted_text = extract_text_from_attachments(prepared_attachments)
        if len(extracted_text) > MAX_ATTACHMENT_TEXT:
            extracted_text = extracted_text[:MAX_ATTACHMENT_TEXT]

        if not extracted_text.strip():
            messagebox.showinfo("Adjuntos", "No se pudo extraer texto de los adjuntos.")
            self.log("No se pudo extraer texto de los adjuntos")
            return

        self.log("Generating attachment summary")
        summary = self._summarize_attachments_content(row=row, extracted_text=extracted_text)
        if not summary:
            messagebox.showwarning("Atención", "No se pudo generar el resumen de adjuntos.")
            return

        self._open_summary_review_dialog(
            row=row,
            ai_summary=summary,
            preview_body=extracted_text,
            summary_source="attachment",
            attachment_types=attachment_types,
        )

    @staticmethod
    def _is_summarizable_attachment(attachment: dict[str, str]) -> bool:
        filename = EmailManagerWindow._extract_attachment_filename(str(attachment.get("filename") or ""))
        mime_type = str(attachment.get("mime") or attachment.get("mime_type") or "").strip().lower()

        allowed_ext = SUPPORTED_ATTACHMENT_EXTENSIONS | {".doc"}
        ignored_image_ext = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        image_mime_prefixes = ("image/",)

        if filename:
            lowered_name = filename.lower()
            suffix = Path(lowered_name).suffix
            if lowered_name.startswith("~"):
                return False
            if suffix in ignored_image_ext:
                return False

            ignored_names = {"logo", "logos", "firma", "signature", "signatures"}
            stem = Path(lowered_name).stem
            if stem in ignored_names:
                return False

            ignored_tokens = ("firma", "signature")
            if any(token in lowered_name for token in ignored_tokens):
                return False

            if suffix in allowed_ext:
                return True
            if mime_type == "application/octet-stream" and suffix in allowed_ext:
                return True

        if any(mime_type.startswith(prefix) for prefix in image_mime_prefixes):
            return False

        supported_mimes = {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/csv",
            "text/plain",
        }
        if mime_type in supported_mimes:
            return True

        return mime_type == "application/octet-stream" and bool(filename and Path(filename.lower()).suffix in allowed_ext)

    @staticmethod
    def _extract_attachment_filename(raw_label: str) -> str:
        value = str(raw_label or "").strip()
        if not value:
            return ""

        if "[" in value and "]" in value:
            value = value.split("[", 1)[0]

        value = Path(value.strip()).name
        value = value.strip().strip('"').strip("'")

        return value.strip()

    def _summarize_attachments_content(self, row: dict[str, str], extracted_text: str) -> str:
        sender = row.get("real_sender") or row.get("sender", "")
        prompt = (
            f"{ATTACHMENT_SUMMARY_REQUEST}\n\n"
            "EMAIL_SUBJECT:\n"
            f"{(row.get('subject') or '').strip()}\n\n"
            "EMAIL_SENDER:\n"
            f"{(sender or '').strip()}\n\n"
            "ATTACHMENT_CONTENT:\n"
            f"{(extracted_text or '').strip()[:MAX_ATTACHMENT_TEXT]}"
        )
        try:
            client = build_openai_client()
            response = client.responses.create(model="gpt-4.1-mini", input=prompt)
            return str(response.output_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.log(f"No se pudo generar resumen de adjuntos: {exc}", level="WARNING")
            return ""

    def _resolve_reply_attachment_paths(self, gmail_id: str, attachments: list[dict[str, str]]) -> list[str] | None:
        if not attachments:
            self.log("Sin adjuntos")
            return []
        decision = self._ask_attach_original_files(len(attachments))
        if decision is None or decision == "none":
            self.log("Sin adjuntos")
            return []
        paths: list[str] = []
        for attachment in attachments:
            try:
                local_path = self.attachment_cache.ensure_downloaded(gmail_id, attachment)
                attachment["local_path"] = local_path
                self.log(f"Adjunto descargado en: {local_path}")
                paths.append(local_path)
            except Exception as exc:  # noqa: BLE001
                self.log(f"Error preparando adjunto {attachment.get('filename', '')}: {exc}", level="ERROR")
                messagebox.showerror("Adjuntos", str(exc))
                return None
        self.log(f"Adjuntos incluidos: {len(paths)}")
        return paths

    def _ask_attach_original_files(self, count: int) -> str | None:
        include = messagebox.askyesno("Adjuntos", f"¿Adjuntar {count} archivos?")
        return "all" if include else "none"

    def _select_original_attachment_paths(self, attachments: list[dict[str, str]]) -> list[str]:
        selection_dialog = tk.Toplevel(self)
        apply_app_icon(selection_dialog)
        selection_dialog.title("Seleccionar adjuntos")
        selection_dialog.transient(self)
        selection_dialog.grab_set()
        ttk.Label(selection_dialog, text="Selecciona adjuntos originales para incluir:").pack(padx=12, pady=(12, 6), anchor="w")

        vars_by_path: list[tuple[tk.BooleanVar, str]] = []
        for attachment in attachments:
            local_path = attachment.get("local_path", "")
            if not local_path:
                continue
            variable = tk.BooleanVar(value=False)
            filename = attachment.get("filename", "") or Path(local_path).name
            ttk.Checkbutton(selection_dialog, text=filename, variable=variable).pack(anchor="w", padx=12, pady=2)
            vars_by_path.append((variable, local_path))

        selected: list[str] = []

        def submit() -> None:
            selected.extend([path for variable, path in vars_by_path if variable.get()])
            selection_dialog.destroy()

        ttk.Button(selection_dialog, text="Aceptar", command=submit).pack(pady=(8, 12))
        self.wait_window(selection_dialog)
        return selected

    def _is_trainable_response(self, response_text: str, category: str) -> bool:
        normalized_text = (response_text or "").strip()
        if len(normalized_text) <= 50:
            return False

        if (category or "").strip().lower() == "notificaciones":
            return False

        simplified = re.sub(r"[^a-záéíóúüñ0-9\s]", " ", normalized_text.lower())
        words = [word for word in simplified.split() if word]
        if not words:
            return False

        trivial_words = {"ok", "recibido", "vale", "gracias"}
        return not all(word in trivial_words for word in words)

    def _open_response_review_dialog(self, row: dict[str, str], draft_ai_response: str) -> None:
        dialog = tk.Toplevel(self)
        apply_app_icon(dialog)
        dialog.title("Respuesta propuesta por IA")
        dialog.geometry("900x700")
        dialog.minsize(900, 700)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        ttk.Label(dialog, text="RESPUESTA PROPUESTA POR IA", font=("Segoe UI", 11, "bold")).grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=12,
            pady=(12, 6),
        )

        output_frame = ttk.Frame(dialog)
        output_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        output_frame.grid_columnconfigure(0, weight=1)
        output_frame.grid_rowconfigure(0, weight=1)

        original_output = self._apply_user_signature(draft_ai_response).strip()
        current_output = original_output
        editor = self._render_output_widget(output_frame, current_output)
        input_original = build_email_training_input_text(
            subject=row.get("subject", ""),
            sender=row.get("real_sender") or row.get("sender", ""),
            body_text=row.get("body_text", ""),
        )

        panel = RefinamientoPanel(
            dialog,
            texto_base=original_output,
            refinement_mode=REFINEMENT_MODE_RESPONSE,
            original_context=input_original,
            on_restore_version=lambda _value: None,
            max_refinements=MAX_REFINEMENTS,
        )
        panel.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        panel.seed_history(original_output)
        refinement_instructions_used: list[str] = []

        def get_current_output() -> str:
            nonlocal current_output
            if editor is not None:
                current_output = editor.get("1.0", "end").strip()
            return current_output

        def set_current_output(value: str) -> None:
            nonlocal current_output, editor
            current_output = (value or "").strip()
            editor = self._render_output_widget(output_frame, current_output)

        panel.on_restore_version = set_current_output

        def refine_response() -> None:
            if not panel.can_refine():
                return

            current_output = get_current_output()
            panel.texto_base = current_output
            instruction = panel.get_prompt_final()
            refined = self._refine_generated_output(
                input_original=input_original,
                output_actual=current_output,
                user_instruction=instruction,
            )
            if not refined:
                return
            set_current_output(refined)
            refinement_instructions_used.append(instruction)
            panel.record_version(refined)
            self.training_repo.save_refinement_history(
                dataset="email_response",
                input_original=input_original,
                output_original=current_output,
                user_instruction=instruction,
                refined_output=refined,
                refinement_mode=REFINEMENT_MODE_RESPONSE,
            )

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))

        ttk.Button(buttons, text="Mejorar resultado", command=refine_response).pack(side="left")
        ttk.Button(
            buttons,
            text="Restablecer",
            command=lambda: (
                set_current_output(original_output),
                panel.clear_refinements(),
            ),
        ).pack(side="left", padx=6)
        ttk.Button(buttons, text="Restaurar versión", command=panel.restore_selected_version).pack(side="left")

        def use_response() -> None:
            final_text = get_current_output()
            if not final_text:
                messagebox.showwarning("Atención", "La respuesta no puede estar vacía.")
                return
            self.response_text.delete("1.0", "end")
            self.response_text.insert("1.0", final_text)
            self._save_email_response_feedback_async(row=row, output_text=final_text, edited_by_user=final_text != original_output)
            self._save_email_reply_learning_async(
                input_email=input_original,
                ai_output=original_output,
                user_final=final_text,
                refinement_instructions="\n".join(refinement_instructions_used).strip(),
            )
            dialog.destroy()

        def edit_and_use_response() -> None:
            edited_text = get_current_output()
            if not edited_text:
                messagebox.showwarning("Atención", "La respuesta no puede estar vacía.")
                return
            self.response_text.delete("1.0", "end")
            self.response_text.insert("1.0", edited_text)
            self._save_email_response_feedback_async(row=row, output_text=edited_text, edited_by_user=True)
            self._save_email_reply_learning_async(
                input_email=input_original,
                ai_output=original_output,
                user_final=edited_text,
                refinement_instructions="\n".join(refinement_instructions_used).strip(),
            )
            dialog.destroy()

        def save_final_version() -> None:
            final_text = get_current_output()
            if not final_text:
                messagebox.showwarning("Atención", "La respuesta no puede estar vacía.")
                return
            self.response_text.delete("1.0", "end")
            self.response_text.insert("1.0", final_text)
            self._save_email_response_feedback_async(row=row, output_text=final_text, edited_by_user=final_text != original_output)
            self._save_email_reply_learning_async(
                input_email=input_original,
                ai_output=original_output,
                user_final=final_text,
                refinement_instructions="\n".join(refinement_instructions_used).strip(),
            )
            dialog.destroy()

        ttk.Button(buttons, text="Confirmar respuesta", command=use_response).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Editar", command=edit_and_use_response).pack(side="left", padx=6)
        ttk.Button(buttons, text="💾 Guardar aprendizaje", command=save_final_version).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right")

        self.wait_window(dialog)

    def _open_summary_review_dialog(
        self,
        row: dict[str, str],
        ai_summary: str,
        preview_body: str,
        summary_source: str = "email",
        attachment_types: list[str] | None = None,
    ) -> None:
        dialog = tk.Toplevel(self)
        apply_app_icon(dialog)
        dialog.title("Resumen generado")
        dialog.geometry("900x700")
        dialog.minsize(900, 700)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        ttk.Label(dialog, text="RESUMEN GENERADO", font=("Segoe UI", 11, "bold")).grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=12,
            pady=(12, 6),
        )

        original_output = ai_summary.strip()
        output_frame = ttk.Frame(dialog)
        output_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        output_frame.grid_columnconfigure(0, weight=1)
        output_frame.grid_rowconfigure(0, weight=1)
        current_output = original_output
        editor = self._render_output_widget(output_frame, current_output)

        input_original = preview_body.strip() or build_email_training_input_text(
            subject=row.get("subject", ""),
            sender=row.get("real_sender") or row.get("sender", ""),
            body_text=row.get("body_text", ""),
        )

        panel = RefinamientoPanel(
            dialog,
            texto_base=original_output,
            refinement_mode=REFINEMENT_MODE_ATTACHMENT_SUMMARY if summary_source == "attachment" else REFINEMENT_MODE_EMAIL_SUMMARY,
            original_context=input_original,
            on_restore_version=lambda _value: None,
            max_refinements=MAX_REFINEMENTS,
        )
        panel.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        panel.seed_history(original_output)
        refinement_instructions_used: list[str] = []

        def get_current_output() -> str:
            nonlocal current_output
            if editor is not None:
                current_output = editor.get("1.0", "end").strip()
            return current_output

        def set_current_output(value: str) -> None:
            nonlocal current_output, editor
            current_output = (value or "").strip()
            editor = self._render_output_widget(output_frame, current_output)

        panel.on_restore_version = set_current_output

        def refine_summary() -> None:
            if not panel.can_refine():
                return
            current_output = get_current_output()
            panel.texto_base = current_output
            instruction = panel.get_prompt_final()
            refined = self._refine_generated_output(
                input_original=input_original,
                output_actual=current_output,
                user_instruction=instruction,
            )
            if not refined:
                return
            set_current_output(refined)
            refinement_instructions_used.append(instruction)
            panel.record_version(refined)
            self.training_repo.save_refinement_history(
                dataset="email_summary",
                input_original=input_original,
                output_original=current_output,
                user_instruction=instruction,
                refined_output=refined,
                refinement_mode=REFINEMENT_MODE_ATTACHMENT_SUMMARY if summary_source == "attachment" else REFINEMENT_MODE_EMAIL_SUMMARY,
            )

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))

        ttk.Button(buttons, text="Mejorar resultado", command=refine_summary).pack(side="left")
        ttk.Button(buttons, text="Restablecer", command=lambda: (set_current_output(original_output), panel.clear_refinements())).pack(side="left", padx=6)
        ttk.Button(buttons, text="Restaurar versión", command=panel.restore_selected_version).pack(side="left")

        def confirm_summary() -> None:
            current_summary = get_current_output()
            if not current_summary:
                messagebox.showwarning("Atención", "El resumen no puede estar vacío.")
                return
            self.response_text.delete("1.0", "end")
            self.response_text.insert("1.0", f"Resumen rápido:\n\n{current_summary}\n")
            self._update_prepared_context_summary(row=row, summary_source=summary_source, summary_value=current_summary)
            self._save_email_summary_feedback_async(
                row=row,
                output_text=current_summary,
                edited_by_user=current_summary != original_output,
                preview_body=preview_body,
                summary_source=summary_source,
                attachment_types=attachment_types,
            )
            self._save_email_summary_learning_async(
                input_email=input_original,
                ai_output=original_output,
                user_final=current_summary,
                refinement_instructions="\n".join(refinement_instructions_used).strip(),
            )
            dialog.destroy()

        def edit_summary() -> None:
            edited_summary = get_current_output()
            if not edited_summary:
                messagebox.showwarning("Atención", "El resumen no puede estar vacío.")
                return
            self.response_text.delete("1.0", "end")
            self.response_text.insert("1.0", f"Resumen rápido:\n\n{edited_summary}\n")
            self._update_prepared_context_summary(row=row, summary_source=summary_source, summary_value=edited_summary)
            self._save_email_summary_feedback_async(
                row=row,
                output_text=edited_summary,
                edited_by_user=True,
                preview_body=preview_body,
                summary_source=summary_source,
                attachment_types=attachment_types,
            )
            dialog.destroy()

        def save_final_version() -> None:
            final_summary = get_current_output()
            if not final_summary:
                messagebox.showwarning("Atención", "El resumen no puede estar vacío.")
                return
            self.response_text.delete("1.0", "end")
            self.response_text.insert("1.0", f"Resumen rápido:\n\n{final_summary}\n")
            self._update_prepared_context_summary(row=row, summary_source=summary_source, summary_value=final_summary)
            self._save_email_summary_feedback_async(
                row=row,
                output_text=final_summary,
                edited_by_user=final_summary != original_output,
                preview_body=preview_body,
                summary_source=summary_source,
                attachment_types=attachment_types,
            )
            dialog.destroy()

        ttk.Button(buttons, text="Confirmar resumen", command=confirm_summary).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Editar", command=edit_summary).pack(side="left", padx=6)
        ttk.Button(buttons, text="💾 Guardar aprendizaje", command=save_final_version).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right")

        self.wait_window(dialog)

    def _save_email_response_feedback_async(self, row: dict[str, str], output_text: str, edited_by_user: bool) -> None:
        sender = row.get("real_sender") or row.get("sender", "")
        metadata = self._build_training_metadata(
            row=row,
            edited_by_user=edited_by_user,
            extra={"email_category": row.get("category", "")},
        )
        input_text = build_email_training_input_text(
            subject=row.get("subject", ""),
            sender=sender,
            body_text=row.get("body_text", ""),
        )
        self._enqueue_training_example_save(
            dataset="email_response",
            input_text=input_text,
            output_text=output_text,
            label=row.get("category", ""),
            metadata=metadata,
            source="interactive_response_review",
            jsonl_dataset="email_reply",
            jsonl_metadata={
                "email_id": str(row.get("gmail_id", "") or "").strip(),
                "edited_by_user": bool(edited_by_user),
            },
        )

    def _save_learning_feedback_async(
        self,
        *,
        learning_type: str,
        input_email: str,
        ai_output: str,
        user_final: str,
        refinement_instructions: str,
    ) -> None:
        def worker() -> None:
            result = self.global_learning_store.guardar_feedback(
                tipo=learning_type,
                input_text=input_email,
                ai_output=ai_output,
                user_final=user_final,
                instrucciones=refinement_instructions,
            )
            if bool(result.get("saved")):
                self.after(0, lambda: self.status_var.set("✔ Aprendizaje guardado correctamente"))
                self.after(0, lambda: self.system_log("✔ Aprendizaje guardado correctamente"))
                return
            reason = str(result.get("reason") or "unknown")
            self.after(0, lambda: self.system_log(f"Aprendizaje omitido ({reason})", level="WARNING"))

        threading.Thread(target=worker, daemon=True).start()

    def _save_email_summary_learning_async(
        self,
        *,
        input_email: str,
        ai_output: str,
        user_final: str,
        refinement_instructions: str,
    ) -> None:
        self._save_learning_feedback_async(
            learning_type="email_summary",
            input_email=input_email,
            ai_output=ai_output,
            user_final=user_final,
            refinement_instructions=refinement_instructions,
        )

    def _save_email_reply_learning_async(
        self,
        *,
        input_email: str,
        ai_output: str,
        user_final: str,
        refinement_instructions: str,
    ) -> None:
        self._save_learning_feedback_async(
            learning_type="email_reply",
            input_email=input_email,
            ai_output=ai_output,
            user_final=user_final,
            refinement_instructions=refinement_instructions,
        )

    def _save_email_summary_feedback_async(
        self,
        row: dict[str, str],
        output_text: str,
        edited_by_user: bool,
        preview_body: str,
        summary_source: str = "email",
        attachment_types: list[str] | None = None,
    ) -> None:
        sender = row.get("real_sender") or row.get("sender", "")
        if summary_source == "attachment":
            input_text = (
                "EMAIL_SUBJECT:\n"
                f"{(row.get('subject') or '').strip()}\n\n"
                "EMAIL_SENDER:\n"
                f"{(sender or '').strip()}\n\n"
                "ATTACHMENT_CONTENT:\n"
                f"{(preview_body or '').strip()[:MAX_ATTACHMENT_TEXT]}"
            ).strip()
        else:
            input_text = build_email_training_input_text(
                subject=row.get("subject", ""),
                sender=sender,
                body_text=preview_body,
            )

        metadata = self._build_training_metadata(
            row=row,
            edited_by_user=edited_by_user,
            extra={
                "summary_type": "email_summary",
                "summary_source": summary_source,
                "attachment_types": attachment_types or [],
            },
            body_text=preview_body,
        )
        self._enqueue_training_example_save(
            dataset="email_summary",
            input_text=input_text,
            output_text=output_text,
            label=None,
            metadata=metadata,
            source="interactive_summary_review",
            jsonl_dataset="attachment_summary" if summary_source == "attachment" else "email_summary",
            jsonl_metadata={
                "email_id": str(row.get("gmail_id", "") or "").strip(),
                "edited_by_user": bool(edited_by_user),
                "summary_source": summary_source,
            },
        )

    def _build_training_metadata(
        self,
        row: dict[str, str],
        edited_by_user: bool,
        extra: dict[str, object] | None = None,
        body_text: str | None = None,
    ) -> str:
        sender_email = parseaddr(str(row.get("sender", "")))[1].lower()
        sender_domain = sender_email.split("@", 1)[1] if "@" in sender_email else ""
        text_body = body_text if body_text is not None else row.get("body_text", "")
        payload = {
            "sender_domain": sender_domain,
            "email_category": row.get("category", ""),
            "email_length": len((text_body or "").strip()),
            "edited_by_user": bool(edited_by_user),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)

    def _refine_generated_output(self, *, input_original: str, output_actual: str, user_instruction: str) -> str:
        prompt = (
            "CONTEXTO ORIGINAL\n"
            f"{(input_original or '').strip()}\n\n"
            "RESULTADO ACTUAL\n"
            f"{(output_actual or '').strip()}\n\n"
            "INSTRUCCIÓN DEL USUARIO\n"
            f"{(user_instruction or '').strip()}\n\n"
            "INSTRUCCIÓN AL MODELO\n"
            "Mejora el resultado teniendo en cuenta la instrucción del usuario.\n"
            "No repitas información innecesaria.\n"
            "Si el usuario solicita campos específicos, extráelos explícitamente."
        )
        try:
            client = build_openai_client()
            response = client.responses.create(
                model=MODEL_NAME,
                input=[
                    {
                        "role": "system",
                        "content": "Refina textos en español preservando el contexto original. Devuelve solo el texto refinado.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            return str(response.output_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo refinar la salida generada: %s", exc)
            self.status_var.set("No se pudo refinar con IA en este momento.")
            return ""

    def _enqueue_training_example_save(
        self,
        *,
        dataset: str,
        input_text: str,
        output_text: str,
        label: str | None,
        metadata: str,
        source: str,
        jsonl_dataset: str,
        jsonl_metadata: dict[str, object] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                result = self.continuous_learning_service.on_new_training_example(
                    dataset=dataset,
                    input_text=input_text,
                    output_text=output_text,
                    label=label,
                    metadata=metadata,
                    source=source,
                )
                self._save_jsonl_training_example_async(
                    dataset=jsonl_dataset,
                    input_text=input_text,
                    output_text=output_text,
                    metadata=jsonl_metadata,
                )
                inserted = bool(result.get("inserted"))
                if not inserted:
                    reason = str(result.get("reason") or "duplicate")
                    if reason in {"duplicate", "near_duplicate"}:
                        logger.info("Training example duplicate ignored")
                        self.after(0, lambda: self.system_log("Duplicate example ignored", level="WARNING"))
                    return

                logger.info("Training example saved for %s", dataset)
                logger.info("Dataset %s marked dirty", dataset)
                self.after(0, lambda: self.system_log(f"Training example saved for {dataset}"))
                self.after(0, lambda: self.system_log(f"Dataset {dataset} marked dirty"))

                state_message = f"Dataset actualizado. {result.get('pending_examples', 'N/A')} nuevos ejemplos pendientes de entrenamiento."
                self.after(0, lambda: self.system_log(state_message))

                full_retrain = result.get("full_retrain", {})
                if bool(full_retrain.get("scheduled")):
                    self.after(
                        0,
                        lambda: self.system_log(
                            "Se han acumulado suficientes ejemplos. El sistema entrenará automáticamente el modelo."
                        ),
                    )
                    self.after(0, lambda: self.system_log("Entrenamiento automático iniciado…"))
                if str(full_retrain.get("reason") or "") == "training_in_progress":
                    self.after(0, lambda: self.system_log("Entrenamiento automático ya en curso."))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error saving training example in background: %s", exc)

        threading.Thread(target=worker, daemon=True).start()

    def _save_jsonl_training_example_async(
        self,
        *,
        dataset: str,
        input_text: str,
        output_text: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            self.ml_training_manager.save_training_example(
                dataset=dataset,
                input_text=input_text,
                output_text=output_text,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo guardar ejemplo JSONL ML: %s", exc)

    @staticmethod
    def _extract_subject_keywords(subject: str) -> str:
        tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", (subject or "").lower())
        stopwords = {
            "de",
            "la",
            "el",
            "los",
            "las",
            "y",
            "o",
            "en",
            "para",
            "por",
            "con",
            "del",
            "al",
            "un",
            "una",
            "re",
            "fw",
            "fwd",
            "rv",
        }
        relevant: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if len(token) <= 2 or token in stopwords or token in seen:
                continue
            seen.add(token)
            relevant.append(token)
            if len(relevant) == 10:
                break
        return ", ".join(relevant)

    def _resolve_default_value(self, category: str, setting_name: str, fallback: str) -> str:
        try:
            settings = self.note_service.get_settings()
            configured = str(getattr(settings, setting_name, "") or "").strip()
            if configured:
                return configured

            values = self.note_service.get_master_values(category)
            if values:
                return values[0]
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo resolver valor por defecto para %s", category)

        return fallback

    def _resolve_my_email(self) -> str:
        try:
            managed = str(self.note_service.get_settings().managed_email or "").strip()
            if managed:
                return managed
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo leer correo gestionado desde configuración")
        profile_email = self.user_profile_repo.get_profile().get("email", "").strip()
        if profile_email:
            return profile_email
        try:
            resolved = self.gmail_client.get_my_email().strip()
            return resolved or USER_EMAIL
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo obtener el email del usuario desde Gmail")
            return USER_EMAIL

    def _apply_user_signature(self, text: str) -> str:
        profile = self.user_profile_repo.get_profile()
        replacements = {
            "[Tu Nombre]": profile.get("nombre", ""),
            "[Tu Cargo]": profile.get("cargo", ""),
            "[Tu Empresa]": profile.get("empresa", ""),
            "[Tu Teléfono]": profile.get("telefono", ""),
        }

        result = text
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value or "")
        return result

    @staticmethod
    def _compose_note_text(subject: str | None, sender: str | None, body_text: str | None, body_html: str | None) -> str:
        text_body = (body_text or "").strip() or EmailManagerWindow._html_to_text(body_html or "")
        return (
            f"Asunto: {(subject or '').strip()}\n"
            f"Remitente: {(sender or '').strip()}\n\n"
            f"{text_body}"
        ).strip()

    @staticmethod
    def _resolve_note_date(received_at: str | None) -> str:
        if not received_at:
            return datetime.utcnow().date().isoformat()

        try:
            return datetime.fromisoformat(received_at.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return datetime.utcnow().date().isoformat()

    @staticmethod
    def _format_datetime(value: str | None) -> str:
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return value

    @staticmethod
    def _html_to_text(content: str) -> str:
        if not content:
            return ""
        no_tags = re.sub(r"<[^>]+>", " ", content)
        normalized = re.sub(r"\s+", " ", no_tags).strip()
        return html.unescape(normalized)

    @staticmethod
    def _extract_quick_summary(response_text: str | None) -> str:
        text = (response_text or "").strip()
        marker = "Resumen rápido:"
        if marker not in text:
            return ""
        _, _, summary = text.partition(marker)
        return summary.strip()

    @staticmethod
    def _compose_note_body_with_summary(email_text: str, summary_text: str) -> str:
        normalized_email = (email_text or "").strip()
        if "EMAIL ORIGINAL\n--------------" in normalized_email:
            return normalized_email
        normalized_summary = (summary_text or "").strip()
        if not normalized_summary:
            return normalized_email
        return (
            "RESUMEN RÁPIDO\n"
            "--------------\n"
            f"{normalized_summary}\n\n"
            "EMAIL ORIGINAL\n"
            "--------------\n"
            f"{normalized_email}"
        ).strip()

    def _get_email_text_for_note(self, row: sqlite3.Row) -> str:
        return self._get_email_original_text(row)

    def _prepare_context_for_selected_email(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para preparar contexto.")
            return

        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return
        merged_content = self._prepare_context_for_row(row)
        if not merged_content:
            messagebox.showwarning("Atención", "No se pudo preparar contexto para el correo seleccionado.")
            return
        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", merged_content)

    def _prepare_context_for_row(self, row: sqlite3.Row | dict[str, str]) -> str:
        gmail_id = str((row.get("gmail_id") if hasattr(row, "get") else row["gmail_id"]) or "").strip()
        if not gmail_id:
            return ""

        self.system_log(f"Preparando contexto para email: {gmail_id}")
        context = self._get_or_create_prepared_context(gmail_id)

        email_summary = context.get("email_summary", "").strip()
        if not email_summary:
            email_summary = self._generate_email_summary_for_context(row)
            if email_summary:
                self.system_log("Resumen de email generado para contexto")

        attachment_summary = context.get("attachment_summary", "").strip()
        if not attachment_summary:
            attachment_summary = self._generate_attachment_summary_for_context(row)
            if attachment_summary:
                self.system_log("Resumen de adjuntos generado para contexto")

        email_original = self._get_email_original_text(row)
        merged_content = self._build_prepared_context_content(
            email_summary=email_summary,
            attachment_summary=attachment_summary,
            email_original=email_original,
        )

        self._prepared_context_by_gmail_id[gmail_id] = {
            "email_summary": email_summary,
            "attachment_summary": attachment_summary,
            "email_original": email_original,
            "merged_content": merged_content,
            "updated_at": datetime.utcnow().isoformat(),
        }
        self.system_log("Contexto preparado actualizado")
        return merged_content

    @staticmethod
    def _build_prepared_context_content(
        email_summary: str = "",
        attachment_summary: str = "",
        email_original: str = "",
    ) -> str:
        sections: list[str] = []
        if email_summary.strip():
            sections.append(f"RESUMEN DEL EMAIL\n-----------------\n{email_summary.strip()}")
        if attachment_summary.strip():
            sections.append(f"RESUMEN DE ADJUNTOS\n-------------------\n{attachment_summary.strip()}")
        if email_original.strip():
            sections.append(f"EMAIL ORIGINAL\n--------------\n{email_original.strip()}")
        return "\n\n".join(sections).strip()

    def _get_or_create_prepared_context(self, gmail_id: str) -> dict[str, str]:
        context = self._prepared_context_by_gmail_id.get(gmail_id)
        if context is None:
            context = {
                "email_summary": "",
                "attachment_summary": "",
                "email_original": "",
                "merged_content": "",
                "updated_at": "",
            }
            self._prepared_context_by_gmail_id[gmail_id] = context
        return context

    def _get_prepared_context_for_gmail_id(self, gmail_id: str) -> dict[str, str] | None:
        context = self._prepared_context_by_gmail_id.get(str(gmail_id or "").strip())
        if not context:
            return None
        if not (context.get("merged_content") or "").strip():
            return None
        return context

    def _prompt_prepare_context_if_missing(self, gmail_id: str, row: sqlite3.Row) -> dict[str, str] | None:
        context = self._get_prepared_context_for_gmail_id(gmail_id)
        if context is not None:
            return context

        should_prepare = messagebox.askyesno(
            "Preparar contexto",
            "¿Deseas preparar automáticamente el contexto (resumen email + adjuntos + original) antes de continuar?",
        )
        if not should_prepare:
            self.system_log("No existe contexto preparado; usando flujo actual")
            return None
        self._prepare_context_for_row(row)
        return self._get_prepared_context_for_gmail_id(gmail_id)

    def _get_email_original_text(self, row: sqlite3.Row | dict[str, str]) -> str:
        body_text = str(row.get("body_text") if hasattr(row, "get") else row["body_text"] or "").strip()
        if body_text:
            return body_text
        body_html = str(row.get("body_html") if hasattr(row, "get") else row["body_html"] or "").strip()
        return self._html_to_text(body_html)

    def _generate_email_summary_for_context(self, row: sqlite3.Row | dict[str, str]) -> str:
        preview_body = self._get_email_original_text(row)
        if not preview_body:
            return ""
        prompt = self._build_email_summary_prompt(preview_body)
        try:
            client = build_openai_client()
            response = client.responses.create(model="gpt-4.1-mini", input=prompt)
            return str(response.output_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.log(f"No se pudo generar resumen de email para contexto: {exc}", level="WARNING")
            return ""

    def _generate_attachment_summary_for_context(self, row: sqlite3.Row | dict[str, str]) -> str:
        gmail_id = str((row.get("gmail_id") if hasattr(row, "get") else row["gmail_id"]) or "").strip()
        if not gmail_id:
            return ""
        attachments = self._build_email_attachments(gmail_id)
        useful_attachments = [item for item in attachments if self._is_summarizable_attachment(item)]
        if not useful_attachments:
            return ""

        prepared_attachments: list[dict[str, str]] = []
        for attachment in useful_attachments:
            filename = self._extract_attachment_filename(str(attachment.get("filename") or "adjunto")) or "adjunto"
            try:
                local_path = self.attachment_cache.ensure_downloaded(gmail_id, attachment)
                attachment["local_path"] = local_path
                prepared_attachments.append(
                    {
                        "file_path": local_path,
                        "local_path": local_path,
                        "filename": filename,
                        "mime_type": str(attachment.get("mime") or attachment.get("mime_type") or ""),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"No se pudo leer adjunto {filename}: {exc}", level="WARNING")

        if not prepared_attachments:
            return ""
        extracted_text = extract_text_from_attachments(prepared_attachments)
        if len(extracted_text) > MAX_ATTACHMENT_TEXT:
            extracted_text = extracted_text[:MAX_ATTACHMENT_TEXT]
        if not extracted_text.strip():
            return ""
        return self._summarize_attachments_content(row=dict(row), extracted_text=extracted_text)

    def _update_prepared_context_summary(self, row: dict[str, str], summary_source: str, summary_value: str) -> None:
        gmail_id = str(row.get("gmail_id") or "").strip()
        if not gmail_id:
            return
        context = self._get_or_create_prepared_context(gmail_id)
        if summary_source == "attachment":
            context["attachment_summary"] = (summary_value or "").strip()
        else:
            context["email_summary"] = (summary_value or "").strip()
        email_original = context.get("email_original") or self._get_email_original_text(row)
        context["email_original"] = email_original
        context["merged_content"] = self._build_prepared_context_content(
            email_summary=context.get("email_summary", ""),
            attachment_summary=context.get("attachment_summary", ""),
            email_original=email_original,
        )
        context["updated_at"] = datetime.utcnow().isoformat()
        self.system_log("Contexto preparado actualizado")
