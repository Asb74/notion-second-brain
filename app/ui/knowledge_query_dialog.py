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
    answer_question_from_knowledge,
)
from app.services.knowledge_query_service import query_knowledge
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)


class KnowledgeQueryDialog(tk.Toplevel):
    """Ask Knowledge with local search and optional grounded AI answers."""

    def __init__(
        self,
        parent: tk.Misc,
        db_connection: sqlite3.Connection,
        on_open_note: Callable[[int], None] | None = None,
    ):
        super().__init__(parent)
        self.db_connection = db_connection
        self.on_open_note = on_open_note
        self.results_by_iid: dict[str, dict[str, object]] = {}
        self.current_results: list[dict[str, object]] = []
        self._searching = False
        self._answering = False

        self.title("Preguntar a Knowledge")
        apply_app_icon(self)
        self.geometry("1120x720")
        self.minsize(820, 560)
        self.transient(parent.winfo_toplevel())

        self.question_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Escribe una pregunta para localizar notas relevantes.")

        self._build_layout()
        self.question_entry.focus_set()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        form = ttk.Frame(self, padding=(12, 12, 12, 8))
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Pregunta:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.question_entry = ttk.Entry(form, textvariable=self.question_var)
        self.question_entry.grid(row=0, column=1, sticky="ew")
        self.question_entry.bind("<Return>", lambda _event: self.search())
        self.search_button = ttk.Button(form, text="Buscar", command=self.search)
        self.search_button.grid(row=0, column=2, padx=(8, 0))
        self.answer_button = ttk.Button(form, text="Responder con IA", command=self.answer_with_ai, state="disabled")
        self.answer_button.grid(row=0, column=3, padx=(8, 0))

        ttk.Label(
            self,
            text="Resultados:",
            padding=(12, 0, 12, 4),
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=1, column=0, sticky="w")

        body = ttk.PanedWindow(self, orient="vertical")
        body.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))

        results_frame = ttk.Frame(body)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        body.add(results_frame, weight=3)

        columns = ("area", "topic", "type", "match_source", "score", "snippet")
        self.results_tree = ttk.Treeview(results_frame, columns=columns, show="tree headings", selectmode="browse")
        self.results_tree.heading("#0", text="Nota")
        self.results_tree.column("#0", width=230, minwidth=160, anchor="w", stretch=True)
        headings = {
            "area": "Área",
            "topic": "Tema",
            "type": "Tipo",
            "match_source": "Coincidencia",
            "score": "Score",
            "snippet": "Snippet",
        }
        widths = {"area": 105, "topic": 110, "type": 90, "match_source": 105, "score": 65, "snippet": 320}
        for column in columns:
            self.results_tree.heading(column, text=headings[column])
            self.results_tree.column(column, width=widths[column], anchor="w", stretch=column == "snippet")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        self.results_tree.bind("<Double-1>", self._on_result_double_click)
        scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=self.results_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=scrollbar.set)

        answer_frame = ttk.Frame(body)
        answer_frame.columnconfigure(0, weight=1)
        answer_frame.rowconfigure(1, weight=1)
        body.add(answer_frame, weight=2)
        ttk.Label(answer_frame, text="Respuesta IA:", font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(6, 4)
        )
        self.answer_text = tk.Text(answer_frame, height=9, wrap="word", state="disabled")
        self.answer_text.grid(row=1, column=0, sticky="nsew")
        answer_scrollbar = ttk.Scrollbar(answer_frame, orient="vertical", command=self.answer_text.yview)
        answer_scrollbar.grid(row=1, column=1, sticky="ns")
        self.answer_text.configure(yscrollcommand=answer_scrollbar.set)

        ttk.Label(self, textvariable=self.status_var, padding=(12, 0, 12, 12)).grid(row=3, column=0, sticky="ew")

    def search(self) -> None:
        if self._searching:
            return
        question = self.question_var.get().strip()
        if not question:
            messagebox.showwarning("Preguntar a Knowledge", "Escribe una pregunta para buscar.", parent=self)
            return
        self._set_searching(True)
        self.status_var.set("Buscando en Knowledge local...")
        self._clear_results()
        self._set_answer_text("")
        database_path = self._database_path()
        threading.Thread(target=self._query_worker, args=(question, database_path), daemon=True).start()

    def _set_searching(self, searching: bool) -> None:
        self._searching = searching
        self.search_button.configure(state="disabled" if searching else "normal")
        self.question_entry.configure(state="disabled" if searching else "normal")
        self._update_answer_button_state()
        self.configure(cursor="watch" if searching or self._answering else "")

    def _clear_results(self) -> None:
        self.results_by_iid.clear()
        self.current_results = []
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
        enabled = bool(self.current_results) and not self._searching and not self._answering
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

    def _query_worker(self, question: str, database_path: str) -> None:
        conn: sqlite3.Connection | None = None
        try:
            if database_path and database_path != ":memory":
                conn = sqlite3.connect(database_path)
                conn.row_factory = sqlite3.Row
                results = query_knowledge(question, conn=conn)
            else:
                # Fallback for tests or in-memory databases. Real app databases use
                # the file-backed branch above so the UI remains responsive.
                results = query_knowledge(question, conn=self.db_connection)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_QUERY: search failed")
            self.after(0, self._finish_search, None, exc)
            return
        finally:
            if conn is not None:
                conn.close()
        self.after(0, self._finish_search, results, None)

    def _finish_search(self, results: list[dict[str, object]] | None, error: Exception | None) -> None:
        self._set_searching(False)
        if error is not None or results is None:
            self.status_var.set("No se pudo consultar Knowledge.")
            messagebox.showerror("Preguntar a Knowledge", f"No se pudo consultar Knowledge.\n\n{error}", parent=self)
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
                    str(result.get("area") or ""),
                    str(result.get("topic") or ""),
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
            f"{len(results)} coincidencias encontradas. Doble clic para abrir la nota o pulsa Responder con IA."
        )
        self._update_answer_button_state()

    def answer_with_ai(self) -> None:
        if self._answering or self._searching:
            return
        question = self.question_var.get().strip()
        if not question:
            messagebox.showwarning("Preguntar a Knowledge", "Escribe una pregunta para responder.", parent=self)
            return
        if not self.current_results:
            self._set_answer_text("No he encontrado información suficiente en Knowledge para responder con seguridad.")
            self.status_var.set("No hay resultados locales para responder con IA.")
            return
        self._set_answering(True)
        self.status_var.set("Generando respuesta IA usando solo resultados locales...")
        self._set_answer_text("Generando respuesta IA...")
        results_snapshot = [dict(result) for result in self.current_results]
        threading.Thread(target=self._answer_worker, args=(question, results_snapshot), daemon=True).start()

    def _answer_worker(self, question: str, results: list[dict[str, object]]) -> None:
        try:
            payload = answer_question_from_knowledge(question, results)
        except KnowledgeAnswerConfigError as exc:
            self.after(0, self._finish_answer, None, exc)
            return
        except KnowledgeAnswerGenerationError as exc:
            logger.exception("KNOWLEDGE_ANSWER: generation failed")
            self.after(0, self._finish_answer, None, exc)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_ANSWER: unexpected UI worker error")
            self.after(0, self._finish_answer, None, exc)
            return
        self.after(0, self._finish_answer, payload, None)

    def _finish_answer(self, payload: dict[str, object] | None, error: Exception | None) -> None:
        self._set_answering(False)
        if error is not None or payload is None:
            if isinstance(error, KnowledgeAnswerConfigError):
                message = "No hay configuración IA disponible."
            else:
                message = f"No se pudo generar la respuesta IA.\n\n{error}"
            self._set_answer_text(message)
            self.status_var.set(message.split("\n", maxsplit=1)[0])
            return
        answer = str(payload.get("answer") or "").strip()
        self._set_answer_text(answer)
        self.status_var.set("Respuesta IA generada usando solo Knowledge local.")

    @staticmethod
    def _format_match_source(match_source: object) -> str:
        labels = {
            "título": "Título",
            "etiquetas": "Etiquetas",
            "adjunto": "Adjunto",
            "contenido": "Contenido",
            "metadatos": "Metadatos",
        }
        return labels.get(str(match_source or ""), str(match_source or ""))

    def _on_result_double_click(self, _event: tk.Event | None = None) -> None:
        selection = self.results_tree.selection()
        if not selection:
            return
        result = self.results_by_iid.get(str(selection[0]))
        if not result:
            return
        note_id = int(result.get("note_id") or 0)
        if note_id <= 0:
            return
        logger.info("KNOWLEDGE_QUERY: open note_id=%s", note_id)
        if self.on_open_note is not None:
            self.on_open_note(note_id)
