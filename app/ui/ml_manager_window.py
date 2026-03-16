"""ML manager Tkinter window."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from app.persistence.email_repository import EmailRepository
from app.persistence.ml_training_repository import MLTrainingRepository
from app.ml.retraining_service import DatasetRetrainingService
from app.ml.dataset_state_service import DatasetStateService

logger = logging.getLogger(__name__)


class MLManagerWindow(tk.Toplevel):
    """Visual explorer and maintenance panel for ML training examples."""

    def __init__(
        self,
        master: tk.Misc,
        db_connection: sqlite3.Connection,
        dataset_filter: str | None = None,
        label_filter: str | None = None,
    ):
        super().__init__(master)
        self.title("ML Manager")
        self.geometry("1280x820")
        self.minsize(980, 620)

        self.repo = MLTrainingRepository(db_connection)
        self.email_repo = EmailRepository(db_connection)
        self.retraining_service = DatasetRetrainingService(db_connection, self.email_repo)
        self.dataset_state_service = DatasetStateService(db_connection)

        self._dataset_selected = ""
        self._example_id_by_item: dict[str, int] = {}
        self._dataset_by_item: dict[str, str] = {}
        self._initial_dataset_filter = dataset_filter
        self._initial_label_filter = label_filter

        self.dataset_filter_var = tk.StringVar(value="")
        self.label_filter_var = tk.StringVar(value="")
        self.source_filter_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")

        self._build_layout()
        self._bind_events()
        self.refresh_all()
        self._apply_initial_filters_if_needed()
        logger.info("ML Manager abierto")

    def _apply_initial_filters_if_needed(self) -> None:
        dataset_filter = self._initial_dataset_filter
        label_filter = self._initial_label_filter
        if dataset_filter is None and label_filter is None:
            return

        logger.info(
            "ML Manager abierto con filtros iniciales: dataset=%s label=%s",
            dataset_filter,
            label_filter,
        )
        self.apply_filters(dataset=dataset_filter, label=label_filter)

    def _build_layout(self) -> None:
        wrapper = ttk.Frame(self, padding=10)
        wrapper.pack(fill="both", expand=True)

        summary_frame = ttk.LabelFrame(wrapper, text="Resumen datasets")
        summary_frame.pack(fill="x")
        self.dataset_tree = ttk.Treeview(summary_frame, columns=("dataset", "total", "last_updated", "state"), show="headings", height=5)
        for column, label, width in [
            ("dataset", "dataset", 220),
            ("total", "total ejemplos", 110),
            ("last_updated", "última actualización", 190),
            ("state", "estado aprendizaje", 420),
        ]:
            self.dataset_tree.heading(column, text=label)
            self.dataset_tree.column(column, width=width, anchor="w")
        self.dataset_tree.pack(fill="x", padx=6, pady=6)

        filters = ttk.LabelFrame(wrapper, text="Filtros")
        filters.pack(fill="x", pady=(8, 0))
        ttk.Label(filters, text="dataset").grid(row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        self.dataset_filter = ttk.Combobox(filters, textvariable=self.dataset_filter_var, state="readonly", width=28)
        self.dataset_filter.grid(row=0, column=1, padx=4, pady=8, sticky="w")

        ttk.Label(filters, text="label").grid(row=0, column=2, padx=(14, 4), pady=8, sticky="w")
        self.label_filter = ttk.Combobox(filters, textvariable=self.label_filter_var, state="readonly", width=22)
        self.label_filter.grid(row=0, column=3, padx=4, pady=8, sticky="w")

        ttk.Label(filters, text="source").grid(row=0, column=4, padx=(14, 4), pady=8, sticky="w")
        self.source_filter = ttk.Combobox(filters, textvariable=self.source_filter_var, state="readonly", width=20)
        self.source_filter.grid(row=0, column=5, padx=4, pady=8, sticky="w")

        ttk.Label(filters, text="buscar").grid(row=0, column=6, padx=(14, 4), pady=8, sticky="w")
        self.search_entry = ttk.Entry(filters, textvariable=self.search_var, width=30)
        self.search_entry.grid(row=0, column=7, padx=4, pady=8, sticky="ew")
        filters.columnconfigure(7, weight=1)

        ttk.Button(filters, text="Aplicar", command=self.refresh_examples).grid(row=0, column=8, padx=4, pady=8)
        ttk.Button(filters, text="Limpiar", command=self._clear_filters).grid(row=0, column=9, padx=(4, 8), pady=8)

        examples_frame = ttk.LabelFrame(wrapper, text="Ejemplos")
        examples_frame.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("id", "dataset", "label", "source", "created_at", "input_preview", "output_preview")
        self.examples_tree = ttk.Treeview(examples_frame, columns=cols, show="headings", height=10)
        for col, label, width in [
            ("id", "id", 70),
            ("dataset", "dataset", 170),
            ("label", "label", 130),
            ("source", "source", 130),
            ("created_at", "created_at", 170),
            ("input_preview", "input_text", 330),
            ("output_preview", "output_text", 330),
        ]:
            self.examples_tree.heading(col, text=label)
            self.examples_tree.column(col, width=width, anchor="w")
        self.examples_tree.pack(fill="both", expand=True, padx=6, pady=6)

        details_frame = ttk.LabelFrame(wrapper, text="Detalle ejemplo")
        details_frame.pack(fill="both", expand=True, pady=(8, 0))
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(1, weight=1)
        details_frame.rowconfigure(3, weight=1)

        ttk.Label(details_frame, text="Input completo").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.input_text = tk.Text(details_frame, height=5, wrap="word")
        self.input_text.grid(row=1, column=0, sticky="nsew", padx=6)

        ttk.Label(details_frame, text="Output completo").grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.output_text = tk.Text(details_frame, height=5, wrap="word")
        self.output_text.grid(row=3, column=0, sticky="nsew", padx=6)

        self.meta_label = ttk.Label(details_frame, text="label: - | source: -")
        self.meta_label.grid(row=4, column=0, sticky="w", padx=6, pady=(8, 2))
        self.metadata_text = tk.Text(details_frame, height=4, wrap="word")
        self.metadata_text.grid(row=5, column=0, sticky="nsew", padx=6, pady=(0, 6))

        self.stats_label = ttk.Label(wrapper, text="")
        self.stats_label.pack(fill="x", pady=(8, 0))

        duplicates_frame = ttk.LabelFrame(wrapper, text="Duplicados detectados")
        duplicates_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.duplicates_text = tk.Text(duplicates_frame, height=5, wrap="word")
        self.duplicates_text.pack(fill="both", expand=True, padx=6, pady=6)

        actions = ttk.Frame(wrapper)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Eliminar ejemplo", command=self._delete_selected).pack(side="left")
        ttk.Button(actions, text="Reentrenar dataset", command=self._retrain_selected_dataset).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Limpieza automática", command=self._auto_clean_duplicates).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Refrescar", command=self.refresh_all).pack(side="left", padx=(8, 0))

    def _bind_events(self) -> None:
        self.dataset_tree.bind("<<TreeviewSelect>>", self._on_dataset_selected)
        self.examples_tree.bind("<<TreeviewSelect>>", self._on_example_selected)
        self.dataset_filter.bind("<<ComboboxSelected>>", lambda _e: self._refresh_label_and_source_filters())

    def refresh_all(self) -> None:
        self._fill_dataset_summary()
        self._fill_filter_values()
        self.refresh_examples()

    def _fill_dataset_summary(self) -> None:
        self.dataset_tree.delete(*self.dataset_tree.get_children())
        self._dataset_by_item.clear()
        for row in self.repo.list_datasets_summary():
            dataset = str(row["dataset"])
            state = self.dataset_state_service.get_state(dataset)
            if state is None:
                state_text = "sin estado"
            else:
                dirty_text = "dirty" if bool(state["dirty"]) else "clean"
                pending = int(state["pending_examples_count"] or 0)
                error = str(state["last_error"] or "").strip()
                state_text = f"{dirty_text} | pending={pending}"
                if error:
                    state_text += f" | error={error[:80]}"
            item = self.dataset_tree.insert(
                "",
                "end",
                values=(dataset, row["total"], row["last_updated"] or "", state_text),
            )
            self._dataset_by_item[item] = dataset

    def _fill_filter_values(self) -> None:
        dataset_values = [""] + self.repo.list_distinct_values("dataset")
        self.dataset_filter.configure(values=dataset_values)
        self._refresh_label_and_source_filters()

    def _refresh_label_and_source_filters(self) -> None:
        dataset = self.dataset_filter_var.get().strip() or None
        self.label_filter.configure(values=[""] + self.repo.list_distinct_values("label", dataset))
        self.source_filter.configure(values=[""] + self.repo.list_distinct_values("source", dataset))

    def _on_dataset_selected(self, _event: object) -> None:
        selected = self.dataset_tree.selection()
        if not selected:
            return
        dataset = self._dataset_by_item.get(selected[0], "")
        self.dataset_filter_var.set(dataset)
        self._dataset_selected = dataset
        self._refresh_label_and_source_filters()
        self.refresh_examples()

    def refresh_examples(self) -> None:
        dataset = self.dataset_filter_var.get().strip() or None
        label = self.label_filter_var.get().strip() or None
        source = self.source_filter_var.get().strip() or None
        search = self.search_var.get().strip() or None

        self.examples_tree.delete(*self.examples_tree.get_children())
        self._example_id_by_item.clear()

        for row in self.repo.list_examples(dataset=dataset, label=label, source=source, search=search):
            values = (
                row["id"],
                row["dataset"],
                row["label"],
                row["source"],
                row["created_at"],
                row["input_preview"],
                row["output_preview"],
            )
            item = self.examples_tree.insert("", "end", values=values)
            self._example_id_by_item[item] = int(row["id"])

        self._refresh_stats(dataset)
        self._refresh_duplicates_panel(dataset)

    def _refresh_stats(self, dataset: str | None) -> None:
        summaries = self.repo.list_datasets_summary()
        total_general = self.repo.total_examples()
        if not summaries:
            self.stats_label.configure(text="Sin ejemplos en ml_training_examples")
            return

        fragments = [f"Total general: {total_general}"]
        for row in summaries:
            fragments.append(f"{row['dataset']}: {row['total']}")

        dataset_for_labels = dataset or (str(summaries[0]["dataset"]) if summaries else "")
        if dataset_for_labels:
            label_counts = self.repo.count_labels_by_dataset(dataset_for_labels)
            labels_txt = []
            for row in label_counts:
                warning = " (Pocos ejemplos)" if int(row["total"]) < 5 else ""
                labels_txt.append(f"{row['label']}: {row['total']}{warning}")
            if labels_txt:
                fragments.append(f"Etiquetas en {dataset_for_labels} → " + ", ".join(labels_txt))

            state = self.dataset_state_service.get_state(dataset_for_labels)
            if state is not None:
                fragments.append(
                    "Estado → "
                    f"dirty={'sí' if bool(state['dirty']) else 'no'}, "
                    f"pendientes={int(state['pending_examples_count'] or 0)}, "
                    f"último_error={str(state['last_error'] or '-')}"
                )

        self.stats_label.configure(text=" | ".join(fragments))


    def _refresh_duplicates_panel(self, dataset: str | None) -> None:
        self.duplicates_text.delete("1.0", "end")
        if not dataset:
            self.duplicates_text.insert("1.0", "Selecciona un dataset para revisar duplicados.")
            return

        duplicates = self.repo.list_duplicate_examples(dataset)
        if not duplicates:
            self.duplicates_text.insert("1.0", "No se detectaron duplicados.")
            return

        lines = ["Duplicados detectados:"]
        for duplicate in duplicates:
            lines.append(
                f"- index {duplicate['duplicate_index']} duplicates example {duplicate['original_index']} "
                f"(label: {duplicate['label'] or '-'})"
            )
        self.duplicates_text.insert("1.0", "\n".join(lines))

    def _auto_clean_duplicates(self) -> None:
        dataset = self.dataset_filter_var.get().strip() or self._dataset_selected
        if not dataset:
            messagebox.showinfo("ML Manager", "Selecciona un dataset para limpiar duplicados.")
            return

        removed = self.repo.remove_duplicate_examples(dataset)
        logger.info("Limpieza automática de duplicados ejecutada en %s: %s eliminados", dataset, removed)
        messagebox.showinfo("ML Manager", f"Limpieza automática completada. Duplicados eliminados: {removed}")
        self.refresh_all()

    def _on_example_selected(self, _event: object) -> None:
        selected = self.examples_tree.selection()
        if not selected:
            return
        example_id = self._example_id_by_item.get(selected[0])
        if example_id is None:
            return

        row = self.repo.get_example(example_id)
        if row is None:
            return

        self._set_text(self.input_text, str(row["input_text"] or ""))
        self._set_text(self.output_text, str(row["output_text"] or ""))
        self._set_text(self.metadata_text, str(row["metadata"] or ""))
        self.meta_label.configure(text=f"label: {row['label'] or '-'} | source: {row['source'] or '-'}")

    def _delete_selected(self) -> None:
        selected = self.examples_tree.selection()
        if not selected:
            messagebox.showinfo("ML Manager", "Selecciona un ejemplo para eliminar.")
            return

        example_id = self._example_id_by_item.get(selected[0])
        if example_id is None:
            return

        if not messagebox.askyesno("Confirmar", f"¿Eliminar ejemplo {example_id}?"):
            return

        self.repo.delete_example(example_id)
        logger.info("Ejemplo ML eliminado: %s", example_id)
        self.refresh_all()

    def _retrain_selected_dataset(self) -> None:
        dataset = self.dataset_filter_var.get().strip() or self._dataset_selected
        if not dataset:
            messagebox.showinfo("ML Manager", "Selecciona un dataset para reentrenar.")
            return

        result = self._trigger_retrain(dataset)
        logger.info("Reentrenamiento lanzado para dataset: %s", dataset)
        messagebox.showinfo("ML Manager", result)

    def _trigger_retrain(self, dataset: str) -> str:
        result = self.retraining_service.check_and_retrain_dataset(dataset, auto=False)
        return str(result.get("reason") or "No se pudo reentrenar el dataset.")


    def apply_filters(self, dataset: str | None = None, label: str | None = None) -> None:
        if dataset is not None:
            dataset_values = tuple(str(value) for value in self.dataset_filter.cget("values"))
            normalized_dataset = dataset.strip()
            if normalized_dataset in dataset_values:
                self.dataset_filter_var.set(normalized_dataset)
                self._dataset_selected = normalized_dataset
            else:
                logger.info("Filtro dataset ignorado por no existir: %s", dataset)
            self._refresh_label_and_source_filters()
        if label is not None:
            label_values = tuple(str(value) for value in self.label_filter.cget("values"))
            normalized_label = label.strip()
            if normalized_label in label_values:
                self.label_filter_var.set(normalized_label)
            else:
                self.label_filter_var.set("")
                logger.info("Filtro label ignorado por no existir en dataset actual: %s", label)
        self.refresh_examples()

    def trigger_retrain(self, dataset: str) -> str:
        return self._trigger_retrain(dataset)

    def _clear_filters(self) -> None:
        self.dataset_filter_var.set("")
        self.label_filter_var.set("")
        self.source_filter_var.set("")
        self.search_var.set("")
        self._refresh_label_and_source_filters()
        self.refresh_examples()

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
