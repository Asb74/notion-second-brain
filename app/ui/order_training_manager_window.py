"""Tkinter window for supervised review of order extraction examples."""

from __future__ import annotations

import json
import logging
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from app.persistence.order_training_repository import OrderTrainingRepository
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)

STATUS_FILTERS = ["pending", "reviewed", "approved", "discarded", "todos"]


class OrderTrainingManagerWindow(tk.Toplevel):
    """Review and correct order extraction examples without touching saved orders."""

    def __init__(self, master: tk.Misc, db_connection: sqlite3.Connection):
        super().__init__(master)
        self.db_connection = db_connection
        self.repository = OrderTrainingRepository(db_connection)
        self.selected_example_id: int | None = None
        self.examples_tree: ttk.Treeview | None = None
        self.status_filter_var = tk.StringVar(value="pending")

        self.title("Revisión de extracción de pedidos")
        apply_app_icon(self)
        self.geometry("1280x780")
        self.minsize(1000, 620)
        self.transient(master)

        self._build_ui()
        self.refresh_examples()

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=8)
        container.pack(fill="both", expand=True)

        paned = ttk.PanedWindow(container, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        filter_row = ttk.Frame(parent)
        filter_row.pack(fill="x", pady=(0, 8))
        ttk.Label(filter_row, text="Estado:").pack(side="left")
        status_combo = ttk.Combobox(
            filter_row,
            textvariable=self.status_filter_var,
            values=STATUS_FILTERS,
            state="readonly",
            width=14,
        )
        status_combo.pack(side="left", padx=(6, 0))
        status_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_examples())

        columns = ("id", "status", "numero_pedido", "source_file", "created_at")
        self.examples_tree = ttk.Treeview(parent, columns=columns, show="headings", height=18)
        headings = {
            "id": "ID",
            "status": "Estado",
            "numero_pedido": "Pedido",
            "source_file": "Archivo",
            "created_at": "Fecha",
        }
        widths = {"id": 60, "status": 90, "numero_pedido": 110, "source_file": 220, "created_at": 150}
        for column in columns:
            self.examples_tree.heading(column, text=headings[column])
            self.examples_tree.column(column, width=widths[column], anchor="w", stretch=column == "source_file")
        self.examples_tree.pack(fill="both", expand=True)
        self.examples_tree.bind("<<TreeviewSelect>>", self._on_example_selected)

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Actualizar", command=self.refresh_examples).grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(buttons, text="Aprobar", command=self.approve_selected).grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(buttons, text="Descartar", command=self.discard_selected).grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(buttons, text="Eliminar", command=self.delete_selected).grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(buttons, text="Guardar corrección", command=self.save_correction).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2
        )
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(2, weight=1)

        self.pdf_text = self._build_text_section(parent, "Texto PDF extraído", 0, editable=False)
        self.extracted_json_text = self._build_text_section(parent, "JSON extraído", 1, editable=False)
        self.corrected_json_text = self._build_text_section(parent, "JSON corregido (editable)", 2, editable=True)

    def _build_text_section(self, parent: ttk.Frame, label: str, row: int, *, editable: bool) -> ScrolledText:
        frame = ttk.LabelFrame(parent, text=label, padding=6)
        frame.grid(row=row, column=0, sticky="nsew", pady=(0, 8 if row < 2 else 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = ScrolledText(frame, wrap="word", height=8, undo=editable)
        text.grid(row=0, column=0, sticky="nsew")
        if not editable:
            text.configure(state="disabled")
        return text

    def refresh_examples(self) -> None:
        tree = self._tree()
        tree.delete(*tree.get_children())
        status = self.status_filter_var.get()
        rows = self.repository.list_examples(None if status == "todos" else status)
        for row in rows:
            tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["id"],
                    row["status"] or "",
                    row["numero_pedido"] or "",
                    row["source_file"] or "",
                    row["created_at"] or "",
                ),
            )

    def _on_example_selected(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        selected_id = self._selected_id()
        if selected_id is None:
            return
        row = self.repository.get_example(selected_id)
        if row is None:
            return
        self.selected_example_id = selected_id
        self._set_text(self.pdf_text, row["pdf_text"] or "", editable=False)
        extracted_json = self._pretty_json_text(row["extracted_json"] or "")
        corrected_source = row["corrected_json"] or row["extracted_json"] or ""
        corrected_json = self._pretty_json_text(corrected_source)
        self._set_text(self.extracted_json_text, extracted_json, editable=False)
        self._set_text(self.corrected_json_text, corrected_json, editable=True)

    def save_correction(self) -> None:
        example_id = self._require_selected_id()
        if example_id is None:
            return
        corrected = self._parse_corrected_json()
        if corrected is None:
            return
        self.repository.update_corrected_json(example_id, corrected)
        logger.info("ORDER_TRAINING: corrección guardada id=%s", example_id)
        messagebox.showinfo("Corrección guardada", "La corrección se ha guardado como ejemplo revisado.")
        self.refresh_examples()
        self._restore_selection(example_id)

    def approve_selected(self) -> None:
        example_id = self._require_selected_id()
        if example_id is None:
            return
        row = self.repository.get_example(example_id)
        if row is None:
            return
        if not (row["corrected_json"] or "").strip():
            source_json = row["extracted_json"] or ""
            try:
                corrected = json.loads(source_json)
            except json.JSONDecodeError as exc:
                messagebox.showerror("JSON inválido", f"No se pudo usar el JSON extraído como corrección.\n\n{exc}")
                return
            self.repository.update_corrected_json(example_id, corrected, notes=row["notes"] or "")
        self.repository.mark_status(example_id, "approved")
        logger.info("ORDER_TRAINING: ejemplo aprobado id=%s", example_id)
        messagebox.showinfo("Ejemplo aprobado", "El ejemplo se ha marcado como aprobado.")
        self.refresh_examples()
        self._restore_selection(example_id)

    def discard_selected(self) -> None:
        example_id = self._require_selected_id()
        if example_id is None:
            return
        self.repository.mark_status(example_id, "discarded")
        logger.info("ORDER_TRAINING: ejemplo descartado id=%s", example_id)
        messagebox.showinfo("Ejemplo descartado", "El ejemplo se ha marcado como descartado.")
        self.refresh_examples()
        self._restore_selection(example_id)

    def delete_selected(self) -> None:
        example_id = self._require_selected_id()
        if example_id is None:
            return
        if not messagebox.askyesno("Eliminar ejemplo", "¿Eliminar definitivamente este ejemplo de entrenamiento?"):
            return
        self.repository.delete_example(example_id)
        self.selected_example_id = None
        self._clear_detail_texts()
        self.refresh_examples()

    def _parse_corrected_json(self) -> dict[str, Any] | list[Any] | None:
        raw = self.corrected_json_text.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning("JSON requerido", "El JSON corregido no puede estar vacío.")
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            messagebox.showerror("JSON inválido", f"El JSON corregido no es válido.\n\n{exc}")
            return None
        if not isinstance(parsed, (dict, list)):
            messagebox.showerror("JSON inválido", "El JSON corregido debe ser un objeto o una lista.")
            return None
        return parsed

    def _selected_id(self) -> int | None:
        selection = self._tree().selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _require_selected_id(self) -> int | None:
        selected_id = self._selected_id() or self.selected_example_id
        if selected_id is None:
            messagebox.showwarning("Sin selección", "Selecciona un ejemplo de entrenamiento.")
            return None
        return selected_id

    def _restore_selection(self, example_id: int) -> None:
        tree = self._tree()
        iid = str(example_id)
        if tree.exists(iid):
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)
            self._on_example_selected(None)

    def _clear_detail_texts(self) -> None:
        self._set_text(self.pdf_text, "", editable=False)
        self._set_text(self.extracted_json_text, "", editable=False)
        self._set_text(self.corrected_json_text, "", editable=True)

    @staticmethod
    def _set_text(widget: ScrolledText, value: str, *, editable: bool) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        if not editable:
            widget.configure(state="disabled")

    @staticmethod
    def _pretty_json_text(raw: str) -> str:
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except (TypeError, json.JSONDecodeError):
            return raw or ""

    def _tree(self) -> ttk.Treeview:
        if self.examples_tree is None:
            raise RuntimeError("Treeview de ejemplos no inicializado")
        return self.examples_tree
