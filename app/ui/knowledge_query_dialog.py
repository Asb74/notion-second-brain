"""Tkinter dialog for local natural-language Knowledge queries."""

from __future__ import annotations

import logging
import sqlite3
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from app.services.knowledge_answer_service import (
    KnowledgeAnswerConfigError,
    KnowledgeAnswerGenerationError,
    answer_question_from_federated_results,
)
from app.services.federated_search_service import emails_available, search_federated
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)


class KnowledgeQueryDialog(tk.Toplevel):
    """Ask Knowledge with local search and optional grounded AI answers."""

    def __init__(
        self,
        parent: tk.Misc,
        db_connection: sqlite3.Connection,
        on_open_note: Callable[[int], None] | None = None,
        on_open_email: Callable[[str], bool] | None = None,
        on_create_note_from_email: Callable[[str], bool] | None = None,
    ):
        super().__init__(parent)
        self.db_connection = db_connection
        self.on_open_note = on_open_note
        self.on_open_email = on_open_email
        self.on_create_note_from_email = on_create_note_from_email
        self.results_by_iid: dict[str, dict[str, object]] = {}
        self.sources_by_iid: dict[str, dict[str, object]] = {}
        self.current_results: list[dict[str, object]] = []
        self.current_answer_sources: list[dict[str, object]] = []
        self._searching = False
        self._answering = False

        self.title("Preguntar a Knowledge")
        apply_app_icon(self)
        self.geometry("1120x720")
        self.minsize(820, 560)
        self.transient(parent.winfo_toplevel())

        self.question_var = tk.StringVar()
        self.include_knowledge_var = tk.BooleanVar(value=True)
        self.emails_available = emails_available(self.db_connection)
        self.include_emails_var = tk.BooleanVar(value=self.emails_available)
        status = "Escribe una pregunta para localizar Knowledge y emails locales."
        if not self.emails_available:
            status = (
                "Escribe una pregunta. No hay emails locales disponibles para buscar."
            )
        self.status_var = tk.StringVar(value=status)

        self._build_layout()
        self.question_entry.focus_set()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        form = ttk.Frame(self, padding=(12, 12, 12, 8))
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Pregunta:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.question_entry = ttk.Entry(form, textvariable=self.question_var)
        self.question_entry.grid(row=0, column=1, sticky="ew")
        self.question_entry.bind("<Return>", lambda _event: self.search())
        self.search_button = ttk.Button(form, text="Buscar", command=self.search)
        self.search_button.grid(row=0, column=2, padx=(8, 0))
        self.answer_button = ttk.Button(
            form, text="Responder con IA", command=self.answer_with_ai, state="disabled"
        )
        self.answer_button.grid(row=0, column=3, padx=(8, 0))

        source_frame = ttk.Frame(self, padding=(12, 0, 12, 8))
        source_frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(source_frame, text="Buscar en:").pack(side="left", padx=(0, 8))
        self.knowledge_check = ttk.Checkbutton(
            source_frame, text="Knowledge", variable=self.include_knowledge_var
        )
        self.knowledge_check.pack(side="left", padx=(0, 12))
        self.emails_check = ttk.Checkbutton(
            source_frame, text="Emails", variable=self.include_emails_var
        )
        self.emails_check.pack(side="left")
        if not self.emails_available:
            self.emails_check.configure(state="disabled")
            ttk.Label(
                source_frame, text="No hay emails locales disponibles para buscar."
            ).pack(side="left", padx=(12, 0))

        ttk.Label(
            self,
            text="Resultados:",
            padding=(12, 0, 12, 4),
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=2, column=0, sticky="w")

        body = ttk.PanedWindow(self, orient="vertical")
        body.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))

        results_frame = ttk.Frame(body)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        body.add(results_frame, weight=3)

        columns = (
            "source",
            "subtitle",
            "date",
            "type",
            "match_source",
            "score",
            "snippet",
        )
        self.results_tree = ttk.Treeview(
            results_frame, columns=columns, show="tree headings", selectmode="browse"
        )
        self.results_tree.heading("#0", text="Título / Asunto")
        self.results_tree.column(
            "#0", width=230, minwidth=160, anchor="w", stretch=True
        )
        headings = {
            "source": "Origen",
            "subtitle": "Área / Remitente",
            "date": "Tema / Fecha",
            "type": "Tipo",
            "match_source": "Coincidencia",
            "score": "Score",
            "snippet": "Snippet",
        }
        widths = {
            "source": 90,
            "subtitle": 150,
            "date": 130,
            "type": 90,
            "match_source": 120,
            "score": 65,
            "snippet": 320,
        }
        for column in columns:
            self.results_tree.heading(column, text=headings[column])
            self.results_tree.column(
                column, width=widths[column], anchor="w", stretch=column == "snippet"
            )
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        self.results_tree.bind("<Double-1>", self._on_result_double_click)
        scrollbar = ttk.Scrollbar(
            results_frame, orient="vertical", command=self.results_tree.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=scrollbar.set)

        answer_frame = ttk.Frame(body)
        answer_frame.columnconfigure(0, weight=1)
        answer_frame.rowconfigure(1, weight=1)
        answer_frame.rowconfigure(3, weight=1)
        body.add(answer_frame, weight=3)
        ttk.Label(
            answer_frame, text="Respuesta IA:", font=("TkDefaultFont", 10, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(6, 4))
        self.answer_text = tk.Text(
            answer_frame, height=9, wrap="word", state="disabled"
        )
        self.answer_text.grid(row=1, column=0, sticky="nsew")
        answer_scrollbar = ttk.Scrollbar(
            answer_frame, orient="vertical", command=self.answer_text.yview
        )
        answer_scrollbar.grid(row=1, column=1, sticky="ns")
        self.answer_text.configure(yscrollcommand=answer_scrollbar.set)

        sources_header = ttk.Frame(answer_frame)
        sources_header.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(
            sources_header,
            text="Fuentes utilizadas:",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left")
        ttk.Button(
            sources_header,
            text="Abrir fuente",
            command=self._open_selected_answer_source,
        ).pack(side="right")
        ttk.Button(
            sources_header,
            text="Crear nota desde este email",
            command=self._create_note_from_selected_answer_email,
        ).pack(side="right", padx=(0, 8))

        source_columns = ("origin", "location", "date", "match", "snippet")
        self.sources_tree = ttk.Treeview(
            answer_frame,
            columns=source_columns,
            show="tree headings",
            selectmode="browse",
            height=5,
        )
        self.sources_tree.heading("#0", text="Título / Asunto")
        self.sources_tree.column(
            "#0", width=260, minwidth=180, anchor="w", stretch=True
        )
        source_headings = {
            "origin": "Origen",
            "location": "Ubicación / Remitente",
            "date": "Fecha",
            "match": "Coincidencia",
            "snippet": "Snippet",
        }
        source_widths = {
            "origin": 90,
            "location": 170,
            "date": 120,
            "match": 110,
            "snippet": 320,
        }
        for column in source_columns:
            self.sources_tree.heading(column, text=source_headings[column])
            self.sources_tree.column(
                column,
                width=source_widths[column],
                anchor="w",
                stretch=column == "snippet",
            )
        self.sources_tree.grid(row=3, column=0, sticky="nsew")
        self.sources_tree.bind(
            "<Double-1>", lambda _event: self._open_selected_answer_source()
        )
        sources_scrollbar = ttk.Scrollbar(
            answer_frame, orient="vertical", command=self.sources_tree.yview
        )
        sources_scrollbar.grid(row=3, column=1, sticky="ns")
        self.sources_tree.configure(yscrollcommand=sources_scrollbar.set)

        ttk.Label(self, textvariable=self.status_var, padding=(12, 0, 12, 12)).grid(
            row=4, column=0, sticky="ew"
        )

    def search(self) -> None:
        if self._searching:
            return
        question = self.question_var.get().strip()
        if not question:
            messagebox.showwarning(
                "Preguntar a Knowledge",
                "Escribe una pregunta para buscar.",
                parent=self,
            )
            return
        include_knowledge = bool(self.include_knowledge_var.get())
        include_emails = bool(self.include_emails_var.get()) and self.emails_available
        if not include_knowledge and not include_emails:
            messagebox.showwarning(
                "Preguntar a Knowledge",
                "Selecciona al menos un origen para buscar.",
                parent=self,
            )
            return
        self._set_searching(True)
        self.status_var.set("Buscando en orígenes locales...")
        self._clear_results()
        self._set_answer_text("")
        self._show_answer_sources([])
        database_path = self._database_path()
        threading.Thread(
            target=self._query_worker,
            args=(question, database_path, include_knowledge, include_emails),
            daemon=True,
        ).start()

    def _set_searching(self, searching: bool) -> None:
        self._searching = searching
        self.search_button.configure(state="disabled" if searching else "normal")
        self.question_entry.configure(state="disabled" if searching else "normal")
        self._update_answer_button_state()
        self.configure(cursor="watch" if searching or self._answering else "")

    def _clear_results(self) -> None:
        self.results_by_iid.clear()
        self.current_results = []
        self.current_answer_sources = []
        for item_id in self.results_tree.get_children():
            self.results_tree.delete(item_id)
        self._update_answer_button_state()

    def _set_answer_text(self, text: str) -> None:
        self.answer_text.configure(state="normal")
        self.answer_text.delete("1.0", "end")
        self.answer_text.insert("1.0", text)
        self.answer_text.configure(state="disabled")

    def _set_answering(self, answering: bool) -> None:
        self._answering = answering
        self._update_answer_button_state()
        self.configure(cursor="watch" if answering or self._searching else "")

    def _update_answer_button_state(self) -> None:
        enabled = (
            bool(self.current_results) and not self._searching and not self._answering
        )
        if hasattr(self, "answer_button"):
            self.answer_button.configure(state="normal" if enabled else "disabled")

    def _database_path(self) -> str:
        try:
            row = self.db_connection.execute("PRAGMA database_list").fetchone()
        except sqlite3.Error:
            return ""
        if row is None:
            return ""
        return str(row[2] if len(row) > 2 else "")

    def _query_worker(
        self,
        question: str,
        database_path: str,
        include_knowledge: bool,
        include_emails: bool,
    ) -> None:
        conn: sqlite3.Connection | None = None
        try:
            if database_path and database_path != ":memory":
                conn = sqlite3.connect(database_path)
                conn.row_factory = sqlite3.Row
                results = search_federated(
                    question, include_knowledge, include_emails, conn=conn
                )
            else:
                # Fallback for tests or in-memory databases. Real app databases use
                # the file-backed branch above so the UI remains responsive.
                results = search_federated(
                    question, include_knowledge, include_emails, conn=self.db_connection
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("FEDERATED_SEARCH: search failed")
            self.after(0, self._finish_search, None, exc)
            return
        finally:
            if conn is not None:
                conn.close()
        self.after(0, self._finish_search, results, None)

    def _finish_search(
        self, results: list[dict[str, object]] | None, error: Exception | None
    ) -> None:
        self._set_searching(False)
        if error is not None or results is None:
            self.status_var.set("No se pudo consultar Knowledge.")
            messagebox.showerror(
                "Preguntar a Knowledge",
                f"No se pudo consultar Knowledge.\n\n{error}",
                parent=self,
            )
            return
        self._show_results(results)

    def _show_results(self, results: list[dict[str, object]]) -> None:
        self._clear_results()
        self.current_results = list(results)
        if not results:
            self.status_var.set("No se encontraron coincidencias.")
            self._update_answer_button_state()
            return
        for index, result in enumerate(results, start=1):
            iid = f"result:{index}"
            self.results_by_iid[iid] = result
            score = float(result.get("score") or 0.0)
            self.results_tree.insert(
                "",
                "end",
                iid=iid,
                text=str(result.get("title") or "Sin título"),
                values=(
                    "Knowledge" if result.get("source") == "knowledge" else "Email",
                    str(result.get("subtitle") or ""),
                    str(result.get("date") or ""),
                    str(result.get("type") or ""),
                    self._format_match_source(result.get("match_source")),
                    f"{score:.2f}",
                    str(result.get("snippet") or ""),
                ),
            )
        first = self.results_tree.get_children()[0]
        self.results_tree.selection_set(first)
        self.results_tree.focus(first)
        self.status_var.set(
            f"{len(results)} coincidencias encontradas. Doble clic para abrir el resultado o pulsa Responder con IA."
        )
        self._update_answer_button_state()

    def answer_with_ai(self) -> None:
        if self._answering or self._searching:
            return
        question = self.question_var.get().strip()
        if not question:
            messagebox.showwarning(
                "Preguntar a Knowledge",
                "Escribe una pregunta para responder.",
                parent=self,
            )
            return
        answer_results = self._current_results_for_active_sources()
        if not answer_results:
            self._set_answer_text("No hay resultados sobre los que responder.")
            self.status_var.set("No hay resultados sobre los que responder.")
            return
        self._set_answering(True)
        scope = self._answer_scope_label(answer_results)
        self.status_var.set(f"Generando respuesta IA basada en: {scope}...")
        self._set_answer_text(f"Generando respuesta IA basada en: {scope}...")
        federated_results = [dict(result) for result in answer_results[:8]]
        threading.Thread(
            target=self._answer_worker,
            args=(question, federated_results, scope),
            daemon=True,
        ).start()

    def _answer_worker(
        self, question: str, results: list[dict[str, object]], scope: str
    ) -> None:
        try:
            payload = answer_question_from_federated_results(question, results)
        except KnowledgeAnswerConfigError as exc:
            self.after(0, self._finish_answer, None, exc, scope)
            return
        except KnowledgeAnswerGenerationError as exc:
            logger.exception("FEDERATED_ANSWER: generation failed")
            self.after(0, self._finish_answer, None, exc, scope)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("FEDERATED_ANSWER: unexpected UI worker error")
            self.after(0, self._finish_answer, None, exc, scope)
            return
        self.after(0, self._finish_answer, payload, None, scope)

    def _finish_answer(
        self, payload: dict[str, object] | None, error: Exception | None, scope: str
    ) -> None:
        self._set_answering(False)
        if error is not None or payload is None:
            if isinstance(error, KnowledgeAnswerConfigError):
                message = "No hay configuración IA disponible."
            else:
                message = f"No se pudo generar la respuesta IA.\n\n{error}"
            self._set_answer_text(message)
            self.status_var.set(message.split("\n", maxsplit=1)[0])
            return
        sources = self._extract_answer_sources(payload)
        scope = self._answer_scope_label(sources) if sources else scope
        self._show_answer_sources(sources)
        answer = str(payload.get("answer") or "").strip()
        if sources:
            self._set_answer_text(f"Basada en: {scope}\n\n{answer}")
        else:
            self._set_answer_text(
                f"Basada en: {scope}\n\n{answer}\n\nNo hay fuentes navegables disponibles."
            )
        self.status_var.set(f"Respuesta IA generada basada en: {scope}.")

    def _current_results_for_active_sources(self) -> list[dict[str, object]]:
        include_knowledge = bool(self.include_knowledge_var.get())
        include_emails = bool(self.include_emails_var.get()) and self.emails_available
        return [
            result
            for result in self.current_results
            if (result.get("source") == "knowledge" and include_knowledge)
            or (result.get("source") == "email" and include_emails)
        ]

    @staticmethod
    def _answer_scope_label(results: list[dict[str, object]]) -> str:
        has_knowledge = any(result.get("source") == "knowledge" for result in results)
        has_email = any(result.get("source") == "email" for result in results)
        if has_knowledge and has_email:
            return "Knowledge + Emails"
        if has_email:
            return "Solo Emails"
        return "Solo Knowledge"

    def _extract_answer_sources(
        self, payload: dict[str, object]
    ) -> list[dict[str, object]]:
        sources = payload.get("navigable_sources")
        if isinstance(sources, list):
            return [dict(source) for source in sources if isinstance(source, dict)]
        grouped = payload.get("sources")
        if isinstance(grouped, dict):
            combined: list[dict[str, object]] = []
            for key in ("knowledge", "emails"):
                items = grouped.get(key)
                if isinstance(items, list):
                    combined.extend(
                        dict(source) for source in items if isinstance(source, dict)
                    )
            return combined
        return []

    def _show_answer_sources(self, sources: list[dict[str, object]]) -> None:
        self.sources_by_iid.clear()
        self.current_answer_sources = list(sources)
        if not hasattr(self, "sources_tree"):
            return
        for item_id in self.sources_tree.get_children():
            self.sources_tree.delete(item_id)
        for index, source in enumerate(sources, start=1):
            iid = f"source:{index}"
            normalized = self._normalize_answer_source(source)
            self.sources_by_iid[iid] = normalized
            self.sources_tree.insert(
                "",
                "end",
                iid=iid,
                text=str(
                    normalized.get("title") or normalized.get("subject") or "Sin título"
                ),
                values=(
                    "Knowledge" if normalized.get("source") == "knowledge" else "Email",
                    str(normalized.get("location") or ""),
                    str(normalized.get("date") or ""),
                    self._format_match_source(normalized.get("match_source")),
                    str(normalized.get("snippet") or ""),
                ),
            )
        children = self.sources_tree.get_children()
        if children:
            self.sources_tree.selection_set(children[0])
            self.sources_tree.focus(children[0])

    def _normalize_answer_source(self, source: dict[str, object]) -> dict[str, object]:
        source_type = str(source.get("source") or "knowledge").lower()
        if source_type == "email":
            normalized = dict(source)
            normalized["source"] = "email"
            normalized["title"] = (
                source.get("subject") or source.get("title") or "Sin asunto"
            )
            normalized["location"] = (
                source.get("from")
                or source.get("sender")
                or source.get("subtitle")
                or ""
            )
            normalized["raw"] = {
                "body_text": source.get("body") or source.get("snippet") or ""
            }
            return normalized
        normalized = dict(source)
        normalized["source"] = "knowledge"
        normalized["title"] = source.get("title") or "Sin título"
        normalized["location"] = " > ".join(
            str(part)
            for part in (source.get("area"), source.get("topic"))
            if str(part or "").strip()
        )
        return normalized

    def _selected_answer_source(self) -> dict[str, object] | None:
        selection = (
            self.sources_tree.selection() if hasattr(self, "sources_tree") else ()
        )
        if not selection:
            return None
        return self.sources_by_iid.get(str(selection[0]))

    def _open_selected_answer_source(self) -> None:
        source = self._selected_answer_source()
        if source is None:
            messagebox.showinfo(
                "Fuentes utilizadas", "Selecciona una fuente para abrir.", parent=self
            )
            return
        self._open_source(source)

    def _create_note_from_selected_answer_email(self) -> None:
        source = self._selected_answer_source()
        if source is None or source.get("source") != "email":
            messagebox.showinfo(
                "Crear nota", "Selecciona una fuente Email.", parent=self
            )
            return
        gmail_id = str(source.get("id") or "").strip()
        logger.info("FEDERATED_ANSWER: create_note_from_email id=%s", gmail_id)
        if (
            gmail_id
            and self.on_create_note_from_email is not None
            and self.on_create_note_from_email(gmail_id)
        ):
            return
        logger.info(
            "FEDERATED_ANSWER: open_source_error reason=create_note_from_email_unavailable"
        )
        messagebox.showwarning(
            "Crear nota",
            "La fuente ya no está disponible o no se pudo iniciar el flujo existente.",
            parent=self,
        )

    def _open_source(self, source: dict[str, object]) -> None:
        source_type = str(source.get("source") or "knowledge")
        source_id = str(source.get("id") or source.get("note_id") or "").strip()
        logger.info(
            "FEDERATED_ANSWER: open_source source=%s id=%s", source_type, source_id
        )
        try:
            if source_type == "email":
                if (
                    source_id
                    and self.on_open_email is not None
                    and self.on_open_email(source_id)
                ):
                    return
                self._show_email_result_dialog(source)
                return
            note_id = int(source.get("note_id") or source.get("id") or 0)
            if note_id <= 0 or self.on_open_note is None:
                raise ValueError("missing_note_id")
            self.on_open_note(note_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("FEDERATED_ANSWER: open_source_error reason=%s", exc)
            messagebox.showwarning(
                "Fuente no disponible", "La fuente ya no está disponible.", parent=self
            )

    @staticmethod
    def _format_match_source(match_source: object) -> str:
        labels = {
            "título": "Título",
            "titulo": "Título",
            "etiquetas": "Etiquetas",
            "adjunto": "Adjunto",
            "adjuntos": "Adjuntos",
            "contenido": "Contenido",
            "metadatos": "Metadatos",
            "asunto": "Asunto",
            "remitente": "Remitente",
            "destinatarios": "Destinatarios",
            "cuerpo": "Contenido",
        }
        return labels.get(str(match_source or ""), str(match_source or ""))

    def _on_result_double_click(self, _event: tk.Event | None = None) -> None:
        selection = self.results_tree.selection()
        if not selection:
            return
        result = self.results_by_iid.get(str(selection[0]))
        if not result:
            return
        source = str(result.get("source") or "knowledge")
        if source == "email":
            gmail_id = str(result.get("id") or "").strip()
            logger.info("FEDERATED_ANSWER: open_source source=email id=%s", gmail_id)
            if (
                gmail_id
                and self.on_open_email is not None
                and self.on_open_email(gmail_id)
            ):
                return
            self._show_email_result_dialog(result)
            return
        note_id = int(result.get("note_id") or result.get("id") or 0)
        if note_id <= 0:
            return
        logger.info("FEDERATED_ANSWER: open_source source=knowledge id=%s", note_id)
        if self.on_open_note is not None:
            self.on_open_note(note_id)

    def _show_email_result_dialog(self, result: dict[str, object]) -> None:
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        gmail_id = str(result.get("id") or "").strip()
        dialog = tk.Toplevel(self)
        dialog.title("Resultado Email")
        dialog.geometry("760x520")
        dialog.transient(self)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        header = (
            f"Asunto: {result.get('title') or ''}\n"
            f"Remitente: {result.get('subtitle') or result.get('location') or result.get('from') or ''}\n"
            f"Fecha: {result.get('date') or ''}"
        )
        ttk.Label(dialog, text=header, padding=12, justify="left").grid(
            row=0, column=0, sticky="ew"
        )
        text = tk.Text(dialog, wrap="word")
        text.grid(row=1, column=0, sticky="nsew", padx=12)
        text.insert("1.0", str(raw.get("body_text") or result.get("snippet") or ""))
        text.configure(state="disabled")
        buttons = ttk.Frame(dialog, padding=12)
        buttons.grid(row=2, column=0, sticky="ew")

        def open_email() -> None:
            logger.info("FEDERATED_ANSWER: open_source source=email id=%s", gmail_id)
            if self.on_open_email is not None and self.on_open_email(gmail_id):
                dialog.destroy()
            else:
                logger.info(
                    "FEDERATED_ANSWER: open_source_error reason=email_manager_unavailable"
                )
                messagebox.showwarning(
                    "Fuente no disponible",
                    "La fuente ya no está disponible.",
                    parent=dialog,
                )

        def create_note() -> None:
            logger.info("FEDERATED_ANSWER: create_note_from_email id=%s", gmail_id)
            if (
                gmail_id
                and self.on_create_note_from_email is not None
                and self.on_create_note_from_email(gmail_id)
            ):
                dialog.destroy()
            else:
                logger.info(
                    "FEDERATED_ANSWER: open_source_error reason=create_note_from_email_unavailable"
                )
                messagebox.showinfo(
                    "Crear nota",
                    "Abre el gestor de emails y usa el flujo existente para crear la nota.",
                    parent=dialog,
                )

        ttk.Button(buttons, text="Abrir en gestor de emails", command=open_email).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="Crear nota desde email", command=create_note).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="Cerrar", command=dialog.destroy).pack(side="right")
