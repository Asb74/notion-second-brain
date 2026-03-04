"""Email manager Tkinter window."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from app.core.email.gmail_client import GmailClient
from app.core.email.mail_ingestion_service import MailIngestionService
from app.core.models import NoteCreateRequest
from app.core.service import NoteService
from app.persistence.repositories import EmailRepository

logger = logging.getLogger(__name__)


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
        self.email_repo = EmailRepository(db_connection)
        self.mail_ingestion_service = MailIngestionService(gmail_client=gmail_client, db_connection=db_connection)

        self.title("Gestión de Emails")
        self.geometry("980x520")
        self.minsize(860, 420)

        self.status_var = tk.StringVar(value="Listo")
        self.columns = ("gmail_id", "subject", "sender", "received_at", "status")

        self._build_layout()
        self.refresh_emails()

    def _build_layout(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=10, pady=(10, 6))

        ttk.Button(toolbar, text="Descargar nuevos correos", command=self._download_new_emails).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Crear Nota desde Email", command=self._create_note_from_selected_email).pack(side="left")

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.tree = ttk.Treeview(table_frame, columns=self.columns, show="headings", height=14)
        self.tree.heading("gmail_id", text="gmail_id")
        self.tree.heading("subject", text="subject")
        self.tree.heading("sender", text="sender")
        self.tree.heading("received_at", text="received_at")
        self.tree.heading("status", text="status")

        self.tree.column("gmail_id", width=220, anchor="w")
        self.tree.column("subject", width=280, anchor="w")
        self.tree.column("sender", width=200, anchor="w")
        self.tree.column("received_at", width=180, anchor="w")
        self.tree.column("status", width=120, anchor="w")

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")

        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10, pady=(0, 10))

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
            rows = self.email_repo.list_emails_for_manager()
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudieron cargar correos")
            self.status_var.set(f"Error al cargar correos: {exc}")
            messagebox.showerror("Error", f"No se pudieron cargar correos.\n\n{exc}")
            return

        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for row in rows:
            values = (
                row["gmail_id"],
                row["subject"] or "",
                row["sender"] or "",
                row["received_at"] or "",
                row["status"] or "",
            )
            self.tree.insert("", "end", iid=str(row["gmail_id"]), values=values)

        self.status_var.set(f"Correos cargados: {len(rows)}")

    def _create_note_from_selected_email(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Atención", "Selecciona un correo para crear la nota.")
            return

        gmail_id = str(selection[0])
        row = self.email_repo.get_email_content(gmail_id)
        if row is None:
            messagebox.showwarning("Atención", "No se encontró el correo seleccionado.")
            return

        if (row["status"] or "") == "converted_to_note":
            messagebox.showinfo("Emails", "Este correo ya fue convertido a nota.")
            return

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
            note_id, message = self.note_service.create_note(req)
            if note_id is None:
                messagebox.showinfo("Duplicado", message)
                return

            self.email_repo.mark_as_converted_to_note(gmail_id)
            self.refresh_emails()
            messagebox.showinfo("OK", f"Nota {note_id} creada desde email.\n\n{message}")
            self.status_var.set(f"Email {gmail_id} convertido a nota {note_id}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo crear nota desde email %s", gmail_id)
            self.status_var.set(f"Error al crear nota desde email: {exc}")
            messagebox.showerror("Error", f"No se pudo crear la nota.\n\n{exc}")

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
        text_body = (body_text or "").strip() or (body_html or "").strip()
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
