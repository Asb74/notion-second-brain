"""Email manager Tkinter window."""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

from app.core.email.category_manager import CategoryManager
from app.core.email.gmail_client import GmailClient
from app.core.email.forwarded_parser import extract_forwarded_headers
from app.core.email.mail_ingestion_service import MailIngestionService
from app.core.models import NoteCreateRequest
from app.core.outlook.outlook_service import OutlookService
from app.core.service import NoteService
from app.persistence.email_repository import EmailRepository
from app.ui.excel_filter import ExcelTreeFilter

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
        self.gmail_client = gmail_client
        self.email_repo = EmailRepository(db_connection)
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
        self.columns = ("gmail_id", "subject", "sender", "type", "received_at", "status")
        self.column_titles = {
            "gmail_id": "Gmail ID",
            "subject": "Asunto",
            "sender": "Remitente",
            "type": "Tipo",
            "received_at": "Fecha",
            "status": "Estado",
        }
        self._all_rows: list[dict[str, str]] = []
        self._rows_by_id: dict[str, dict[str, str]] = {}
        self._current_tab = default_label

        self._build_layout()
        self.refresh_emails()

    def _build_layout(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Toolbar.TButton", padding=(8, 6))

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 6))
        toolbar.pack(fill="x")

        self._add_toolbar_group(toolbar, [
            ("Descargar", self._download_new_emails),
            ("Reentrenar modelo", self._retrain_model),
            ("➕ Nueva categoría", self._create_category),
        ])
        self._add_toolbar_group(toolbar, [
            ("Seleccionar todo", self._select_all_rows),
            ("Deseleccionar todo", self._clear_selection),
        ])
        self._add_toolbar_group(toolbar, [
            ("Crear nota", self._create_notes_from_selected_emails),
            ("Marcar ignoradas", self._mark_selected_as_ignored),
            ("Eliminar", self._delete_selected_emails),
        ])
        self._add_toolbar_group(toolbar, [
            ("Solo no leídos", self._select_unread_rows),
            ("Solo pedidos", lambda: self._select_rows_by_type("order")),
            ("Solo suscripciones", lambda: self._select_rows_by_type("subscription")),
            ("Limpiar filtros", self._clear_filters),
        ])

        move_group = ttk.Frame(toolbar)
        move_group.pack(side="left", padx=(6, 0))
        ttk.Label(move_group, text="Mover a:").pack(side="left", padx=(0, 4))
        self.move_target_combo = ttk.Combobox(
            move_group,
            textvariable=self.move_target_var,
            values=list(self._move_label_to_type.keys()),
            state="readonly",
            width=14,
        )
        self.move_target_combo.pack(side="left", padx=(0, 4))
        ttk.Button(move_group, text="Aplicar", style="Toolbar.TButton", width=14, command=self._move_selected_emails).pack(side="left")

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
        self.tree.column("sender", width=220, anchor="w")
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
        self.preview_text = tk.Text(preview_frame, wrap="word", height=12)
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=preview_scroll.set)
        self.preview_text.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        self.preview_text.configure(state="disabled")

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

        lower_panel.add(preview_frame, weight=1)
        lower_panel.add(response_frame, weight=1)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status_frame, textvariable=self.model_var, anchor="e").pack(side="right")

    def _add_toolbar_group(self, parent: ttk.Frame, buttons: list[tuple[str, object]]) -> None:
        frame = ttk.Frame(parent)
        frame.pack(side="left", padx=(0, 8))
        for text, command in buttons:
            ttk.Button(frame, text=text, command=command, style="Toolbar.TButton", width=18).pack(side="left", padx=(0, 4))
        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=(0, 8))

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
        try:
            processed_ids = self.mail_ingestion_service.sync_unread_emails()
            self.status_var.set(f"Descarga completada. Nuevos correos: {len(processed_ids)}")
            self.refresh_emails()
            messagebox.showinfo("Emails", f"Se descargaron {len(processed_ids)} correos nuevos.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error descargando correos")
            self.status_var.set(f"Error al descargar correos: {exc}")
            messagebox.showerror("Error", f"No se pudieron descargar correos.\n\n{exc}")

    def _retrain_model(self) -> None:
        trained = self.classifier.retrain_if_possible(force=True)
        examples = self.email_repo.count_labeled_examples()
        self.classifier.examples_count = examples
        reclassified = self.classifier.reclassify_all_emails() if trained else 0
        self.model_var.set(self.classifier.model_status())
        if trained:
            self.status_var.set(f"Modelo reentrenado con {examples} ejemplos. Correos reclasificados: {reclassified}.")
        else:
            self.status_var.set(f"No hay suficientes ejemplos para entrenar ({examples}).")

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
                row["sender"],
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
        preview = "Selecciona un email para ver su contenido."
        if len(selection) == 1:
            row = self._rows_by_id.get(str(selection[0]))
            if row:
                body = row["body_text"].strip() or self._html_to_text(row["body_html"])
                header_block = (
                    f"From real: {row.get('original_from', row['sender'])}\n"
                    f"Reply-To: {row.get('original_reply_to', '') or '-'}\n"
                    f"To: {row.get('original_to', '') or '-'}\n"
                    f"Cc: {row.get('original_cc', '') or '-'}\n"
                    f"Fecha: {self._format_datetime(row['received_at'])}\n"
                )
                preview = (
                    f"{header_block}\n"
                    f"Asunto: {row['subject']}\n"
                    f"Remitente: {row['sender']}\n"
                    f"Tipo: {row['type']}\n"
                    f"Estado: {row['status']}\n\n"
                    f"{body.strip()}"
                )
        elif len(selection) > 1:
            preview = f"{len(selection)} correos seleccionados."

        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", preview)
        self.preview_text.configure(state="disabled")

    def _generate_response(self) -> None:
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("Atención", "Selecciona un solo correo para generar respuesta.")
            return
        row = self._rows_by_id.get(str(selection[0]))
        if not row:
            return

        subject = row["subject"].strip() or "tu mensaje"
        body = (
            "Hola,\n\n"
            f"Gracias por tu correo sobre '{subject}'.\n"
            "Lo he revisado y te responderé con el detalle correspondiente a la mayor brevedad.\n\n"
            "Saludos,"
        )
        self.response_text.delete("1.0", "end")
        self.response_text.insert("1.0", body)

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

        original_from = row.get("original_from", row["sender"])
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

        try:
            self.outlook_service.create_draft(
                subject=draft_subject,
                body=body,
                original_from=original_from,
                original_to=original_to,
                original_cc=original_cc,
                my_email=self.my_email,
                original_reply_to=row.get("original_reply_to", ""),
            )
            self.status_var.set("Borrador de Outlook abierto correctamente.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo crear borrador de Outlook")
            messagebox.showerror("Error", f"No se pudo crear el borrador en Outlook.\n\n{exc}")

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
            return self.gmail_client.get_my_email().strip()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo obtener el email del usuario desde Gmail")
            return ""

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
