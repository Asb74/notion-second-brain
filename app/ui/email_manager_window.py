"""Email manager Tkinter window."""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from app.core.email.gmail_client import GmailClient
from app.core.email.mail_ingestion_service import MailIngestionService
from app.core.models import NoteCreateRequest
from app.core.service import NoteService
from app.persistence.email_repository import EmailRepository
from app.ui.excel_filter import ExcelTreeFilter

logger = logging.getLogger(__name__)


class EmailManagerWindow(tk.Toplevel):
    """Manage ingested emails and manual conversion to notes."""

    TAB_TO_CATEGORY = {"Prioritarios": "priority", "Publicidad": "marketing"}

    def __init__(
        self,
        master: tk.Misc,
        note_service: NoteService,
        db_connection: sqlite3.Connection,
        gmail_client: GmailClient,
    ):
        super().__init__(master)
        self.note_service = note_service
        self.email_repo = EmailRepository(db_connection)
        self.mail_ingestion_service = MailIngestionService(gmail_client=gmail_client, db_connection=db_connection)

        self.title("Gestión de Emails")
        self.geometry("1120x720")
        self.minsize(980, 560)

        self.status_var = tk.StringVar(value="Listo")
        self.columns = ("gmail_id", "subject", "sender", "received_at", "status")
        self.column_titles = {
            "gmail_id": "Gmail ID",
            "subject": "Asunto",
            "sender": "Remitente",
            "received_at": "Fecha",
            "status": "Estado",
        }
        self._all_rows: list[dict[str, str]] = []
        self._rows_by_id: dict[str, dict[str, str]] = {}
        self._current_category = "priority"

        self._build_layout()
        self.refresh_emails()

    def _build_layout(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=10, pady=(10, 6))

        ttk.Button(toolbar, text="Descargar", command=self._download_new_emails).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Crear Nota seleccionadas", command=self._create_notes_from_selected_emails).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Eliminar seleccionadas", command=self._delete_selected_emails).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Marcar como ignoradas", command=self._mark_selected_as_ignored).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Seleccionar todo", command=self._select_all_rows).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Deseleccionar todo", command=self._clear_selection).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Limpiar filtros", command=self._clear_filters).pack(side="left")

        tabs_frame = ttk.Frame(self)
        tabs_frame.pack(fill="x", padx=10, pady=(0, 6))
        self.notebook = ttk.Notebook(tabs_frame)
        self.priority_tab = ttk.Frame(self.notebook)
        self.marketing_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.priority_tab, text="Prioritarios")
        self.notebook.add(self.marketing_tab, text="Publicidad")
        self.notebook.pack(fill="x")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.tree = ttk.Treeview(table_frame, columns=self.columns, show="headings", height=12, selectmode="extended")
        for col in self.columns:
            self.tree.heading(col, text=self.column_titles.get(col, col))

        self.tree.column("gmail_id", width=230, anchor="w")
        self.tree.column("subject", width=330, anchor="w")
        self.tree.column("sender", width=240, anchor="w")
        self.tree.column("received_at", width=160, anchor="w")
        self.tree.column("status", width=130, anchor="w")

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

        preview_frame = ttk.LabelFrame(self, text="Vista previa")
        preview_frame.pack(fill="both", expand=False, padx=10, pady=(0, 6))
        self.preview_text = tk.Text(preview_frame, wrap="word", height=10)
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=preview_scroll.set)
        self.preview_text.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        self.preview_text.configure(state="disabled")

        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10, pady=(0, 10))

    def _on_tab_changed(self, _event: tk.Event) -> None:
        tab_name = self.notebook.tab(self.notebook.select(), "text")
        self._current_category = self.TAB_TO_CATEGORY.get(tab_name, "priority")
        self.refresh_emails()

    def _clear_filters(self) -> None:
        self.excel_filter.clear_all_filters()
        self._refresh_preview()

    def _download_new_emails(self) -> None:
        try:
            processed_ids = self.mail_ingestion_service.sync_unread_emails()
            self.status_var.set(f"Descarga completada. Nuevos correos: {len(processed_ids)}")
            self.refresh_emails()
            messagebox.showinfo("Emails", f"Se descargaron {len(processed_ids)} correos nuevos.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error descargando correos")
            self.status_var.set(f"Error al descargar correos: {exc}")
            messagebox.showerror("Error", f"No se pudieron descargar correos.\n\n{exc}")

    def refresh_emails(self) -> None:
        try:
            rows = self.email_repo.get_emails_by_category(self._current_category)
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
                "received_at": row["received_at"] or "",
                "received_at_display": self._format_datetime(row["received_at"]),
                "body_text": row["body_text"] or "",
                "body_html": row["body_html"] or "",
                "status": row["status"] or "",
                "category": row["category"] or "pending",
            }
            self._all_rows.append(normalized)
            self._rows_by_id[str(row["gmail_id"])] = normalized

        self.excel_filter.apply()
        self.status_var.set(f"Correos cargados ({self._current_category}): {len(rows)}")
        self._refresh_preview()

    def _set_filtered_rows(self, rows: list[dict[str, str]]) -> None:
        selected_ids = set(self.tree.selection())
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for row in rows:
            values = (
                row["gmail_id"],
                row["subject"],
                row["sender"],
                row["received_at_display"],
                row["status"],
            )
            iid = str(row["gmail_id"])
            self.tree.insert("", "end", iid=iid, values=values)
            if iid in selected_ids:
                self.tree.selection_add(iid)

    def _create_notes_from_selected_emails(self) -> None:
        selected_ids = self._selected_ids()
        if not selected_ids:
            messagebox.showwarning("Atención", "Selecciona al menos un correo para crear notas.")
            return

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
                raw_text=self._compose_note_text(row["subject"], row["sender"], row["body_text"], row["body_html"]),
                source="email_pasted",
                area=self._resolve_default_value("Area", "default_area", "General"),
                tipo=self._resolve_default_value("Tipo", "default_tipo", "Nota"),
                estado=self._resolve_default_value("Estado", "default_estado", "Pendiente"),
                prioridad=self._resolve_default_value("Prioridad", "default_prioridad", "Media"),
                fecha=self._resolve_note_date(row["received_at"]),
            )

            try:
                note_id, _message = self.note_service.create_note(req)
                if note_id is None:
                    skipped_count += 1
                    continue
                self.email_repo.update_status(gmail_id, "converted_to_note")
                created_count += 1
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo crear nota desde email %s", gmail_id)
                skipped_count += 1

        self.refresh_emails()
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

    def _clear_selection(self) -> None:
        self.tree.selection_remove(self.tree.selection())
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        selection = self.tree.selection()
        preview = "Selecciona un email para ver su contenido."
        if len(selection) == 1:
            row = self._rows_by_id.get(str(selection[0]))
            if row:
                body = row["body_text"].strip() or self._html_to_text(row["body_html"])
                preview = (
                    f"Asunto: {row['subject']}\n"
                    f"Remitente: {row['sender']}\n"
                    f"Fecha: {self._format_datetime(row['received_at'])}\n"
                    f"Estado: {row['status']}\n\n"
                    f"{body.strip()}"
                )
        elif len(selection) > 1:
            preview = f"{len(selection)} correos seleccionados."

        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", preview)
        self.preview_text.configure(state="disabled")

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
