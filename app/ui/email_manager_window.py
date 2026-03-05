"""Email manager Tkinter window."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import tkinter as tk
import webbrowser
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from tkinterweb import HtmlFrame

from app.config.mail_config import USER_EMAIL
from app.core.email.category_manager import CategoryManager
from app.core.email.gmail_client import GmailClient
from app.core.email.forwarded_parser import extract_forwarded_headers
from app.core.email.mail_ingestion_service import MailIngestionService
from app.core.models import NoteCreateRequest
from app.core.outlook.outlook_service import OutlookService
from app.core.service import NoteService
from app.persistence.email_repository import EmailRepository
from app.persistence.training_repository import TrainingRepository
from app.persistence.user_profile_repository import UserProfileRepository
from app.services.email_entity_extractor import EmailEntityExtractor
from app.ui.excel_filter import ExcelTreeFilter
from app.utils.openai_client import MODEL_NAME, build_openai_client

logger = logging.getLogger(__name__)

_SYSTEM_LOG_WIDGET: ScrolledText | None = None


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
        self.training_repo = TrainingRepository(db_connection)
        self.user_profile_repo = UserProfileRepository(db_connection)
        self.category_manager = CategoryManager(self.email_repo)
        self.mail_ingestion_service = MailIngestionService(gmail_client=gmail_client, db_connection=db_connection)
        self.classifier = self.mail_ingestion_service.classifier
        self.outlook_service = OutlookService()
        self.my_email = self._resolve_my_email()

        self.title("Gestión de Emails")
        self.geometry("1220x760")
        self.minsize(1080, 620)

        self.status_var = tk.StringVar(value="Listo")
        self.model_var = tk.StringVar(value=self.classifier.model_status())
        self._categories = self.category_manager.list_categories()
        default_label = self._categories[0]["display_name"] if self._categories else "Otros"
        self._tab_to_types = {item["display_name"]: [item["name"]] for item in self._categories}
        self._move_label_to_type = {item["display_name"]: item["name"] for item in self._categories}
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
        self._expanded_html_frame: HtmlFrame | None = None
        self._expanded_attachment_preview_window: tk.Toplevel | None = None
        self._expanded_attachment_preview_frame: HtmlFrame | None = None
        self._temp_opened_attachments: list[Path] = []
        self.detected_pedido_var = tk.StringVar(value="")
        self.detected_cliente_var = tk.StringVar(value="")
        self.detected_persona_var = tk.StringVar(value="")
        self.detected_accion_var = tk.StringVar(value="")

        self._build_layout()
        self.refresh_emails()

    def _build_layout(self) -> None:
        global _SYSTEM_LOG_WIDGET

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))

        toolbar_container = ttk.Frame(self, padding=(10, 10, 10, 6))
        toolbar_container.pack(fill="x")
        toolbar_container.columnconfigure(0, weight=1)

        toolbar_main = ttk.Frame(toolbar_container)
        toolbar_main.grid(row=0, column=0, sticky="ew")

        first_row_buttons = [
            ("Descargar", self._download_new_emails),
            ("Reentrenar modelo", self._retrain_model),
            ("Reclasificar emails", self._reclassify_current_emails),
            ("Nueva categoría", self._create_category),
            ("Crear nota", self._create_notes_from_selected_emails),
            ("Marcar ignoradas", self._mark_selected_as_ignored),
            ("Eliminar", self._delete_selected_emails),
        ]
        for idx, (label, command) in enumerate(first_row_buttons):
            ttk.Button(toolbar_main, text=label, command=command, style="Toolbar.TButton").grid(
                row=0,
                column=idx * 2,
                padx=(0, 6),
                sticky="ew",
            )
            toolbar_main.columnconfigure(idx * 2, weight=1, uniform="toolbar-first-row")
            if idx < len(first_row_buttons) - 1:
                ttk.Separator(toolbar_main, orient="vertical").grid(row=0, column=idx * 2 + 1, sticky="ns", padx=(0, 6))

        toolbar_secondary = ttk.Frame(toolbar_container)
        toolbar_secondary.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.filters_menu_button = ttk.Menubutton(toolbar_secondary, text="Filtros", style="Toolbar.TButton")
        self.filters_menu = tk.Menu(self.filters_menu_button, tearoff=0)
        self.filters_menu.add_command(label="Solo no leídos", command=self._select_unread_rows)
        self.filters_menu.add_command(label="Solo pedidos", command=lambda: self._select_rows_by_type("order"))
        self.filters_menu.add_command(label="Solo suscripciones", command=lambda: self._select_rows_by_type("subscription"))
        self.filters_menu_button.configure(menu=self.filters_menu)
        second_row_controls: list[tuple[str, object]] = [
            ("Filtros", self.filters_menu_button),
            ("Limpiar filtros", ttk.Button(toolbar_secondary, text="Limpiar filtros", command=self._clear_filters, style="Toolbar.TButton")),
            ("Seleccionar todo", ttk.Button(toolbar_secondary, text="Seleccionar todo", command=self._select_all_rows, style="Toolbar.TButton")),
            ("Deseleccionar todo", ttk.Button(toolbar_secondary, text="Deseleccionar todo", command=self._clear_selection, style="Toolbar.TButton")),
        ]

        self.move_target_combo = ttk.Combobox(
            toolbar_secondary,
            textvariable=self.move_target_var,
            values=list(self._move_label_to_type.keys()),
            state="readonly",
            width=16,
        )
        second_row_controls.extend(
            [
                ("Mover a", self.move_target_combo),
                ("Aplicar", ttk.Button(toolbar_secondary, text="Aplicar", style="Toolbar.TButton", command=self._move_selected_emails)),
            ]
        )

        for idx, (_name, widget) in enumerate(second_row_controls):
            widget.grid(row=0, column=idx * 2, padx=(0, 6), sticky="ew")
            toolbar_secondary.columnconfigure(idx * 2, weight=1, uniform="toolbar-second-row")
            if idx < len(second_row_controls) - 1:
                ttk.Separator(toolbar_secondary, orient="vertical").grid(row=0, column=idx * 2 + 1, sticky="ns", padx=(0, 6))

        tabs_frame = ttk.Frame(self)
        tabs_frame.pack(fill="x", padx=10, pady=(0, 6))
        self.notebook = ttk.Notebook(tabs_frame)
        self._rebuild_tabs()
        self.notebook.pack(fill="x")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook.bind("<Button-3>", self._open_tab_context_menu)

        self.tab_menu = tk.Menu(self, tearoff=0)
        self.tab_menu.add_command(label="Renombrar categoría", command=self._rename_current_category)
        self.tab_menu.add_command(label="Eliminar categoría", command=self._delete_current_category)

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.tree = ttk.Treeview(table_frame, columns=self.columns, show="headings", height=12, selectmode="extended")
        for col in self.columns:
            self.tree.heading(col, text=self.column_titles.get(col, col))

        self.tree.column("gmail_id", width=210, anchor="w")
        self.tree.column("subject", width=280, anchor="w")
        self.tree.column("real_sender", width=220, anchor="w")
        self.tree.column("type", width=110, anchor="w")
        self.tree.column("received_at", width=160, anchor="w")
        self.tree.column("status", width=120, anchor="w")

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_preview())

        self.excel_filter = ExcelTreeFilter(
            master=self,
            tree=self.tree,
            columns=self.columns,
            column_titles=self.column_titles,
            get_rows=lambda: self._all_rows,
            set_rows=self._set_filtered_rows,
        )

        lower_panel = ttk.PanedWindow(self, orient="horizontal")
        lower_panel.pack(fill="both", expand=False, padx=10, pady=(0, 6))

        preview_frame = ttk.LabelFrame(lower_panel, text="Vista previa")
        html_preview_frame = ttk.Frame(preview_frame)
        html_preview_frame.pack(fill="both", expand=True, padx=4, pady=(4, 2))
        self.html_view = HtmlFrame(html_preview_frame, messages_enabled=False)
        self.html_view.pack(fill="both", expand=True)

        attachments_frame = ttk.LabelFrame(html_preview_frame, text="Adjuntos")
        attachments_frame.pack(fill="x", pady=(6, 2))
        self.attachments_container = ttk.Frame(attachments_frame)
        self.attachments_container.pack(fill="x", padx=6, pady=6)

        preview_actions = ttk.Frame(preview_frame)
        preview_actions.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(preview_actions, text="Expandir vista", command=self._expand_html_view).pack(side="left")

        entities_frame = ttk.LabelFrame(preview_frame, text="Datos detectados")
        entities_frame.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(entities_frame, text="Pedido:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, textvariable=self.detected_pedido_var).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, text="Cliente:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, textvariable=self.detected_cliente_var).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, text="Persona:").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, textvariable=self.detected_persona_var).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, text="Acción:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(entities_frame, textvariable=self.detected_accion_var).grid(row=3, column=1, sticky="w", padx=4, pady=2)

        response_frame = ttk.LabelFrame(lower_panel, text="Respuesta")
        self.response_text = tk.Text(response_frame, wrap="word", height=12)
        response_scroll = ttk.Scrollbar(response_frame, orient="vertical", command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=response_scroll.set)
        self.response_text.pack(side="top", fill="both", expand=True, padx=4, pady=4)
        response_scroll.pack(side="right", fill="y")

        response_actions = ttk.Frame(response_frame)
        response_actions.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(response_actions, text="Generar respuesta", command=self._generate_response).pack(side="left", padx=(0, 6))
        ttk.Button(response_actions, text="Crear borrador Outlook", command=self._create_outlook_draft).pack(side="left")
        ttk.Button(response_actions, text="Reenviar", command=self._forward_email).pack(side="left", padx=(6, 0))

        lower_panel.add(preview_frame, weight=1)
        lower_panel.add(response_frame, weight=1)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status_frame, textvariable=self.model_var, anchor="e").pack(side="right")

        system_status_frame = ttk.LabelFrame(self, text="Estado del sistema")
        system_status_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.system_status_text = ScrolledText(system_status_frame, height=6, state="disabled", wrap="word")
        self.system_status_text.pack(fill="both", expand=True, padx=6, pady=6)
        _SYSTEM_LOG_WIDGET = self.system_status_text

    def _reload_category_maps(self) -> None:
        self._categories = self.category_manager.list_categories()
        self._tab_to_types = {item["display_name"]: [item["name"]] for item in self._categories}
        self._move_label_to_type = {item["display_name"]: item["name"] for item in self._categories}
        labels = list(self._move_label_to_type.keys())
        self.move_target_combo.configure(values=labels)
        if self.move_target_var.get() not in labels and labels:
            self.move_target_var.set(labels[0])

    def _rebuild_tabs(self) -> None:
        labels = [item["display_name"] for item in self._categories]
        current = self._current_tab if self._current_tab in labels else (labels[0] if labels else "")
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        for tab_name in labels:
            self.notebook.add(ttk.Frame(self.notebook), text=tab_name)
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
        self._current_tab = self.notebook.tab(self.notebook.select(), "text")
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
        self._current_tab = self.notebook.tab(self.notebook.select(), "text")
        self.refresh_emails()

    def _clear_filters(self) -> None:
        self.excel_filter.clear_all_filters()
        self._refresh_preview()

    def _download_new_emails(self) -> None:
        system_log("Iniciando descarga de emails")
        try:
            processed_ids = self.mail_ingestion_service.sync_unread_emails()
            system_log(f"Descarga completada. Nuevos correos: {len(processed_ids)}")
            self.status_var.set(f"Descarga completada. Nuevos correos: {len(processed_ids)}")
            self.refresh_emails()
            messagebox.showinfo("Emails", f"Se descargaron {len(processed_ids)} correos nuevos.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error descargando correos")
            system_log(f"Error al descargar correos: {exc}", level="ERROR")
            self.status_var.set(f"Error al descargar correos: {exc}")
            messagebox.showerror("Error", f"No se pudieron descargar correos.\n\n{exc}")

    def _retrain_model(self) -> None:
        examples = self.email_repo.count_labeled_examples()
        categories_count = len(self._categories)
        if examples < 10:
            system_log("No hay suficientes ejemplos para entrenar", level="WARNING")
        else:
            system_log("Iniciando entrenamiento")
            system_log(f"Emails usados: {examples}")
            system_log(f"Categorías detectadas: {categories_count}")

        trained = self.classifier.retrain_if_possible(force=True)
        self.classifier.examples_count = examples
        self.model_var.set(self.classifier.model_status())
        warning = self.classifier.last_training_warning
        if trained:
            system_log("Entrenamiento completado")
            self.status_var.set(f"Modelo reentrenado con {examples} ejemplos. Reclasificación manual disponible.")
            return
        if warning:
            system_log(warning, level="WARNING")
            self.status_var.set(warning)
            return
        system_log(f"No hay suficientes ejemplos para entrenar ({examples}).", level="WARNING")
        self.status_var.set(f"No hay suficientes ejemplos para entrenar ({examples}).")

    def _reclassify_current_emails(self) -> None:
        if not self.classifier.ml_model.is_trained:
            system_log("No hay modelo entrenado para reclasificar.", level="WARNING")
            self.status_var.set("No hay modelo entrenado para reclasificar.")
            return
        system_log("Iniciando reclasificación de emails")
        reclassified = self.classifier.reclassify_all_emails()
        self.refresh_emails()
        system_log(f"Reclasificación completada. Correos actualizados: {reclassified}.")
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
                "real_sender": row["real_sender"] or row["sender"] or "",
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
                "entities_json": row["entities_json"] or "",
            }
            self._all_rows.append(normalized)
            self._rows_by_id[str(row["gmail_id"])] = normalized

        self.excel_filter.apply()
        self.classifier.examples_count = self.email_repo.count_labeled_examples()
        self.model_var.set(self.classifier.model_status())
        self.status_var.set(f"Correos cargados ({self._current_tab}): {len(rows)}")
        self._refresh_preview()

    def _set_filtered_rows(self, rows: list[dict[str, str]]) -> None:
        selected_ids = set(self.tree.selection())
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for row in rows:
            values = (
                row["gmail_id"],
                row["subject"],
                row["real_sender"],
                row["type"],
                row["received_at_display"],
                row["status"],
            )
            iid = str(row["gmail_id"])
            self.tree.insert("", "end", iid=iid, values=values)
            if iid in selected_ids:
                self.tree.selection_add(iid)

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

        self.status_var.set(f"{len(selected_ids)} correos movidos a {target_label}.")
        self.refresh_emails()

    def _create_notes_from_selected_emails(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para crear notas.")
            return
        system_log(f"Iniciando creación de notas para {len(selected_ids)} emails")

        created_count = 0
        skipped_count = 0
        for gmail_id in selected_ids:
            row = self.email_repo.get_email_content(gmail_id)
            if row is None:
                skipped_count += 1
                continue
            if (row["status"] or "") == "converted_to_note":
                skipped_count += 1
                continue

            req = NoteCreateRequest(
                title=(row["subject"] or "").strip(),
                raw_text=self._compose_note_text(
                    row["subject"],
                    row.get("real_sender") or row["sender"],
                    row["body_text"],
                    row["body_html"],
                ),
                source="email_pasted",
                area=self._resolve_default_value("Area", "default_area", "General"),
                tipo=self._resolve_default_value("Tipo", "default_tipo", "Nota"),
                estado=self._resolve_default_value("Estado", "default_estado", "Pendiente"),
                prioridad=self._resolve_default_value("Prioridad", "default_prioridad", "Media"),
                fecha=self._resolve_note_date(row["received_at"]),
            )

            try:
                system_log(f"Analizando nota para email {gmail_id}")
                note_id, _message = self.note_service.create_note(req)
                if note_id is None:
                    system_log(f"No se creó la nota para email {gmail_id}", level="WARNING")
                    skipped_count += 1
                    continue
                tasks_count = self.note_service.actions_repo.pending_count_by_note(note_id)
                system_log(f"Nota creada (ID {note_id})")
                system_log(f"Tareas detectadas: {tasks_count}")
                system_log(f"Tareas creadas: {tasks_count}")
                self.email_repo.update_status(gmail_id, "converted_to_note")
                created_count += 1
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo crear nota desde email %s", gmail_id)
                system_log(f"Error al crear nota desde email {gmail_id}", level="ERROR")
                skipped_count += 1

        self.refresh_emails()
        system_log(f"Creación de notas finalizada. Notas: {created_count}, omitidos: {skipped_count}")
        messagebox.showinfo("Resultado", f"Notas creadas: {created_count}\nOmitidos: {skipped_count}")

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
        detected_entities: dict[str, str] = {}
        if len(selection) == 1:
            row = self._rows_by_id.get(str(selection[0]))
            if row:
                body_html = row.get("body_html", "").strip()
                attachments = self._build_email_attachments(str(row["gmail_id"]))
                detected_entities = self._resolve_entities(row)

        self._set_html_preview(body_html)
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

    def _set_html_preview(self, body_html: str) -> None:
        self._current_html_content = body_html.strip()
        if self._current_html_content:
            self.html_view.load_html(self._current_html_content)
        else:
            self.html_view.load_html("<html><body><p>Sin contenido HTML.</p></body></html>")

        if self._expanded_html_frame is not None:
            self._expanded_html_frame.load_html(self._current_html_content or "<html><body><p>Sin contenido HTML.</p></body></html>")

    def _build_email_attachments(self, gmail_id: str) -> list[dict[str, str]]:
        email_attachments = []
        for attachment in self.email_repo.get_attachments(gmail_id):
            email_attachments.append(
                {
                    "filename": str(attachment["filename"] or ""),
                    "mime": str(attachment["mime_type"] or "application/octet-stream"),
                    "attachment_id": str(attachment["id"]),
                    "local_path": str(attachment["local_path"] or ""),
                }
            )
        return email_attachments

    def _render_attachments(self, attachments: list[dict[str, str]]) -> None:
        for child in self.attachments_container.winfo_children():
            child.destroy()

        if not attachments:
            ttk.Label(self.attachments_container, text="(sin adjuntos)").pack(anchor="w")
            return

        for attachment in attachments:
            row_frame = ttk.Frame(self.attachments_container)
            row_frame.pack(fill="x", pady=2)
            filename = attachment.get("filename", "") or "(sin nombre)"
            local_path = attachment.get("local_path", "")
            ttk.Label(row_frame, text=f"📎 {filename}").pack(side="left")
            ttk.Button(
                row_frame,
                text="Abrir",
                command=lambda current_attachment=attachment: self._open_attachment(current_attachment),
                width=8,
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                row_frame,
                text="Guardar",
                command=lambda path=local_path, name=filename: self._save_attachment_as(path, name),
                width=8,
            ).pack(side="left", padx=(6, 0))
            if self._is_image_attachment(filename, attachment.get("mime", "")):
                ttk.Button(
                    row_frame,
                    text="Vista previa",
                    command=lambda path=local_path, name=filename: self._preview_attachment_image(path, name),
                    width=11,
                ).pack(side="left", padx=(6, 0))

    def _open_attachment(self, attachment: dict[str, str]) -> None:
        local_path = attachment.get("local_path", "")
        path = Path(local_path)
        if not path.exists():
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
            else:
                webbrowser.open(temp_path.resolve().as_uri())
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir adjunto")
            messagebox.showerror("Adjunto", f"No se pudo abrir el adjunto.\n\n{exc}")

    def _save_attachment_as(self, local_path: str, filename: str) -> None:
        source = Path(local_path)
        if not source.exists():
            messagebox.showerror("Adjunto", f"No existe el archivo:\n{local_path}")
            return

        target = filedialog.asksaveasfilename(parent=self, initialfile=filename, title="Guardar adjunto como")
        if not target:
            return

        try:
            shutil.copy2(source, Path(target))
            self.status_var.set(f"Adjunto guardado en: {target}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo guardar adjunto")
            messagebox.showerror("Adjunto", f"No se pudo guardar el adjunto.\n\n{exc}")

    @staticmethod
    def _is_image_attachment(filename: str, mime_type: str) -> bool:
        lowered_name = filename.lower()
        lowered_mime = (mime_type or "").lower()
        return lowered_mime.startswith("image/") or lowered_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))

    def _preview_attachment_image(self, local_path: str, filename: str) -> None:
        path = Path(local_path)
        if not path.exists():
            messagebox.showerror("Adjunto", f"No existe el archivo:\n{local_path}")
            return

        if self._expanded_attachment_preview_window is None or not self._expanded_attachment_preview_window.winfo_exists():
            self._expanded_attachment_preview_window = tk.Toplevel(self)
            self._expanded_attachment_preview_window.geometry("980x680")
            self._expanded_attachment_preview_frame = HtmlFrame(self._expanded_attachment_preview_window, messages_enabled=False)
            self._expanded_attachment_preview_frame.pack(fill="both", expand=True)
            self._expanded_attachment_preview_window.protocol("WM_DELETE_WINDOW", self._close_attachment_preview)

        self._expanded_attachment_preview_window.title(f"Vista previa: {filename}")
        safe_name = html.escape(filename)
        image_uri = path.resolve().as_uri()
        preview_html = (
            "<html><body style='margin:0;padding:8px;background:#f0f0f0;'>"
            f"<h4 style='margin:0 0 8px 0;font-family:Arial, sans-serif;'>{safe_name}</h4>"
            f"<img src='{image_uri}' style='max-width:100%;height:auto;border:1px solid #ccc;background:#fff;' />"
            "</body></html>"
        )
        if self._expanded_attachment_preview_frame is not None:
            self._expanded_attachment_preview_frame.load_html(preview_html)

        self._expanded_attachment_preview_window.lift()
        self._expanded_attachment_preview_window.focus_force()

    def _close_attachment_preview(self) -> None:
        if self._expanded_attachment_preview_window is not None:
            self._expanded_attachment_preview_window.destroy()
        self._expanded_attachment_preview_window = None
        self._expanded_attachment_preview_frame = None

    def _expand_html_view(self) -> None:
        if self._expanded_html_window is None or not self._expanded_html_window.winfo_exists():
            self._expanded_html_window = tk.Toplevel(self)
            self._expanded_html_window.title("Vista HTML expandida")
            self._expanded_html_window.geometry("1100x700")
            self._expanded_html_frame = HtmlFrame(self._expanded_html_window, messages_enabled=False)
            self._expanded_html_frame.pack(fill="both", expand=True)
            self._expanded_html_window.protocol("WM_DELETE_WINDOW", self._close_expanded_html_view)

        if self._expanded_html_frame is not None:
            self._expanded_html_frame.load_html(self._current_html_content or "<html><body><p>Sin contenido HTML.</p></body></html>")

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

        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", self._apply_user_signature(body))

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

        original_from = row.get("real_sender") or row.get("original_from", row["sender"])
        original_to = row.get("original_to", "")
        original_cc = row.get("original_cc", "")

        is_forwarded = bool(re.match(r"^\s*(rv:|fw:|fwd:)", subject, re.IGNORECASE))
        if is_forwarded:
            parsed = extract_forwarded_headers(row.get("body_text", ""))
            if parsed and parsed.get("from"):
                original_from = parsed["from"]
                original_to = "; ".join(parsed.get("to_list", []))
                original_cc = "; ".join(parsed.get("cc_list", []))

        reply_all = messagebox.askyesno("Responder", "¿Responder a todos (incluyendo CC)?")
        if not reply_all:
            original_to = ""
            original_cc = ""

        attachments = self._build_email_attachments(str(row["gmail_id"]))
        attachment_paths = self._resolve_reply_attachment_paths(attachments)
        if attachment_paths is None:
            return

        try:
            self.outlook_service.create_draft(
                subject=draft_subject,
                body=body,
                original_from=original_from,
                original_to=original_to,
                original_cc=original_cc,
                my_email=self.my_email,
                original_reply_to=row.get("original_reply_to", ""),
                attachment_paths=attachment_paths,
            )
            self.status_var.set("Borrador de Outlook abierto correctamente.")

            if self._is_trainable_response(body, row["category"]):
                save = messagebox.askyesno(
                    "Entrenamiento",
                    "¿Deseas guardar esta respuesta como ejemplo para mejorar futuras respuestas?",
                )
                if save:
                    self._save_training_example(row, body)
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo crear borrador de Outlook")
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
        attachment_paths = [attachment["local_path"] for attachment in attachments if attachment.get("local_path")]
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
            self.status_var.set("Borrador de reenvío abierto correctamente.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo crear borrador de reenvío")
            messagebox.showerror("Error", f"No se pudo crear el borrador de reenvío en Outlook.\n\n{exc}")

    def _resolve_reply_attachment_paths(self, attachments: list[dict[str, str]]) -> list[str] | None:
        if not attachments:
            return []

        decision = self._ask_attach_original_files()
        if decision is None:
            return None
        if decision == "none":
            return []
        if decision == "all":
            return [attachment["local_path"] for attachment in attachments if attachment.get("local_path")]
        return self._select_original_attachment_paths(attachments)

    def _ask_attach_original_files(self) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title("Adjuntos")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text="¿Adjuntar archivos originales?").pack(padx=16, pady=(14, 8))
        response: dict[str, str | None] = {"value": None}

        def choose(value: str) -> None:
            response["value"] = value
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(padx=12, pady=(0, 12))
        ttk.Button(buttons, text="Sí", command=lambda: choose("all"), width=12).pack(side="left", padx=4)
        ttk.Button(buttons, text="No", command=lambda: choose("none"), width=12).pack(side="left", padx=4)
        ttk.Button(buttons, text="Seleccionar", command=lambda: choose("select"), width=12).pack(side="left", padx=4)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self.wait_window(dialog)
        return response["value"]

    def _select_original_attachment_paths(self, attachments: list[dict[str, str]]) -> list[str]:
        selection_dialog = tk.Toplevel(self)
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

    def _save_training_example(self, row: dict[str, str], response_text: str) -> None:
        sender_email = parseaddr(row.get("sender", ""))[1].lower()
        sender_domain = sender_email.split("@", 1)[1] if "@" in sender_email else ""
        sender_type = "interno" if "sansebas.es" in sender_domain else "externo"
        subject = row.get("subject", "")
        keywords = self._extract_subject_keywords(subject)
        created_at = datetime.utcnow().isoformat()

        self.training_repo.save_example(
            category=row.get("category", ""),
            sender_type=sender_type,
            original_subject=subject,
            original_body=row.get("body_text", ""),
            response_text=response_text,
            created_at=created_at,
            keywords=keywords,
        )
        total_examples = self.training_repo.conn.execute("SELECT COUNT(*) FROM email_response_examples").fetchone()[0]
        system_log("Ejemplo de respuesta guardado")
        system_log(f"Categoría: {row.get('category', '')}")
        system_log(f"Total ejemplos: {total_examples}")
        logger.info(
            "Ejemplo de entrenamiento guardado para categoría '%s' con remitente '%s'.",
            row.get("category", ""),
            sender_type,
        )

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
