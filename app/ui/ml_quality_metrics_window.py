"""ML quality metrics Tkinter window."""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from app.persistence.ml_training_repository import MLTrainingRepository

logger = logging.getLogger(__name__)


class MLQualityMetricsWindow(tk.Toplevel):
    """Quality panel to inspect training dataset readiness."""

    def __init__(
        self,
        master: tk.Misc,
        db_connection: sqlite3.Connection,
        open_ml_manager_callback: Callable[[str, str | None], None] | None = None,
        retrain_dataset_callback: Callable[[str], str] | None = None,
    ):
        super().__init__(master)
        self.title("ML Quality Metrics")
        self.geometry("1160x840")
        self.minsize(920, 620)

        self.repo = MLTrainingRepository(db_connection)
        self.open_ml_manager_callback = open_ml_manager_callback
        self.retrain_dataset_callback = retrain_dataset_callback

        self.dataset_var = tk.StringVar(value="")
        self._dataset_by_item: dict[str, str] = {}
        self._selected_label = ""

        self.main_metrics_var = tk.StringVar(value="")
        self.dataset_status_var = tk.StringVar(value="")

        self._build_layout()
        self._bind_events()
        self.refresh_all()
        logger.info("ML Quality Metrics abierto")

    def _build_layout(self) -> None:
        wrapper = ttk.Frame(self, padding=10)
        wrapper.pack(fill="both", expand=True)

        summary_frame = ttk.LabelFrame(wrapper, text="Resumen datasets")
        summary_frame.pack(fill="x")
        self.dataset_tree = ttk.Treeview(
            summary_frame,
            columns=("dataset", "total", "distinct_labels", "last_updated", "balance", "status"),
            show="headings",
            height=6,
        )
        headers = [
            ("dataset", "dataset", 210),
            ("total", "total ejemplos", 110),
            ("distinct_labels", "labels", 90),
            ("last_updated", "última actualización", 190),
            ("balance", "balance", 130),
            ("status", "estado", 260),
        ]
        for col, label, width in headers:
            self.dataset_tree.heading(col, text=label)
            self.dataset_tree.column(col, width=width, anchor="w")
        self.dataset_tree.pack(fill="x", padx=6, pady=6)

        selector = ttk.Frame(wrapper)
        selector.pack(fill="x", pady=(8, 0))
        ttk.Label(selector, text="Dataset").pack(side="left")
        self.dataset_combo = ttk.Combobox(selector, textvariable=self.dataset_var, state="readonly", width=35)
        self.dataset_combo.pack(side="left", padx=(8, 0))
        ttk.Button(selector, text="Refrescar", command=self.refresh_all).pack(side="left", padx=(8, 0))

        metrics = ttk.LabelFrame(wrapper, text="Métricas principales")
        metrics.pack(fill="x", pady=(8, 0))
        ttk.Label(metrics, textvariable=self.main_metrics_var, justify="left").pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Label(metrics, textvariable=self.dataset_status_var).pack(anchor="w", padx=8, pady=(0, 6))

        distribution_frame = ttk.LabelFrame(wrapper, text="Distribución de labels")
        distribution_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.distribution_tree = ttk.Treeview(
            distribution_frame,
            columns=("label", "count", "percentage", "status"),
            show="headings",
            height=8,
        )
        for col, label, width in [
            ("label", "label", 240),
            ("count", "count", 110),
            ("percentage", "percentage", 120),
            ("status", "status", 200),
        ]:
            self.distribution_tree.heading(col, text=label)
            self.distribution_tree.column(col, width=width, anchor="w")
        self.distribution_tree.pack(fill="both", expand=True, padx=6, pady=6)

        issues_frame = ttk.LabelFrame(wrapper, text="Problemas detectados")
        issues_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.issues_text = tk.Text(issues_frame, height=6, wrap="word")
        self.issues_text.pack(fill="both", expand=True, padx=6, pady=6)

        recommendations_frame = ttk.LabelFrame(wrapper, text="Recomendaciones")
        recommendations_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.recommendations_text = tk.Text(recommendations_frame, height=6, wrap="word")
        self.recommendations_text.pack(fill="both", expand=True, padx=6, pady=6)

        actions = ttk.Frame(wrapper)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Recomendaciones", command=self._generate_recommendations).pack(side="left")
        ttk.Button(actions, text="Abrir en ML Manager", command=self._open_in_ml_manager).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Reentrenar dataset", command=self._retrain_dataset).pack(side="left", padx=(8, 0))

    def _bind_events(self) -> None:
        self.dataset_tree.bind("<<TreeviewSelect>>", self._on_summary_dataset_selected)
        self.dataset_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_dataset_metrics())
        self.distribution_tree.bind("<<TreeviewSelect>>", self._on_distribution_selected)

    def refresh_all(self) -> None:
        self._fill_summary()
        datasets = [str(row["dataset"]) for row in self.repo.get_dataset_summary()]
        self.dataset_combo.configure(values=datasets)
        if datasets and not self.dataset_var.get().strip():
            self.dataset_var.set(datasets[0])
        self.refresh_dataset_metrics()

    def _fill_summary(self) -> None:
        self.dataset_tree.delete(*self.dataset_tree.get_children())
        self._dataset_by_item.clear()
        for row in self.repo.get_dataset_summary():
            dataset = str(row["dataset"])
            issues = self.repo.get_quality_issues(dataset)
            distribution = self.repo.get_label_distribution(dataset)
            max_pct = max((float(item["percentage"]) for item in distribution), default=0.0)
            balance = "Desbalanceado" if max_pct > 60 else "Equilibrado"
            status = "⚠ Revisar" if issues else "✅ Listo"
            item = self.dataset_tree.insert(
                "",
                "end",
                values=(
                    dataset,
                    row["total"],
                    row["distinct_labels"],
                    row["last_updated"] or "",
                    balance,
                    status,
                ),
            )
            self._dataset_by_item[item] = dataset

    def refresh_dataset_metrics(self) -> None:
        dataset = self.dataset_var.get().strip()
        if not dataset:
            return
        logger.info("Métricas calculadas para dataset: %s", dataset)

        summary = next((row for row in self.repo.get_dataset_summary() if str(row["dataset"]) == dataset), None)
        if summary is None:
            return

        distribution = self.repo.get_label_distribution(dataset)
        weak_labels = [f"{row['label']}: {row['count']}" for row in distribution if str(row["label"]) != "(sin etiqueta)" and int(row["count"]) < 5]
        missing = self.repo.count_incomplete_examples(dataset)
        duplicates = self.repo.count_duplicate_examples(dataset)

        self.main_metrics_var.set(
            "\n".join(
                [
                    f"Dataset: {dataset}",
                    f"Total ejemplos: {summary['total']}",
                    f"Labels distintas: {summary['distinct_labels']}",
                    f"Última actualización: {summary['last_updated'] or '-'}",
                    f"Etiquetas débiles: {', '.join(weak_labels) if weak_labels else 'Ninguna'}",
                    f"Sin label: {int(missing['missing_label'] or 0)} | Sin output_text: {int(missing['missing_output'] or 0)} | Duplicados: {duplicates}",
                ]
            )
        )

        issues = self.repo.get_quality_issues(dataset)
        self.dataset_status_var.set("⚠ Dataset con problemas" if issues else "✅ Dataset listo")

        self._fill_distribution(distribution)
        self._fill_text_widget(self.issues_text, issues, empty_message="No se detectaron problemas.")
        self._fill_text_widget(self.recommendations_text, [], empty_message="Pulsa 'Recomendaciones' para generar acciones.")

    def _fill_distribution(self, distribution: list[sqlite3.Row]) -> None:
        self.distribution_tree.delete(*self.distribution_tree.get_children())
        for row in distribution:
            label = str(row["label"])
            count = int(row["count"])
            pct = float(row["percentage"])
            status = "OK"
            if label == "(sin etiqueta)":
                status = "MISSING LABEL"
            elif count < 5:
                status = "FEW EXAMPLES"
            elif pct > 60:
                status = "UNBALANCED"

            self.distribution_tree.insert("", "end", values=(label, count, f"{pct:.2f}%", status))

    def _on_summary_dataset_selected(self, _event: object) -> None:
        selected = self.dataset_tree.selection()
        if not selected:
            return
        dataset = self._dataset_by_item.get(selected[0], "")
        if dataset:
            self.dataset_var.set(dataset)
            self.refresh_dataset_metrics()

    def _on_distribution_selected(self, _event: object) -> None:
        selected = self.distribution_tree.selection()
        if not selected:
            return
        values = self.distribution_tree.item(selected[0], "values")
        if values:
            self._selected_label = str(values[0])

    def _generate_recommendations(self) -> None:
        dataset = self.dataset_var.get().strip()
        if not dataset:
            return
        recommendations = self.repo.get_recommendations(dataset)
        logger.info("Recomendaciones generadas para dataset: %s", dataset)
        self._fill_text_widget(self.recommendations_text, recommendations, empty_message="Sin recomendaciones.")

    def _open_in_ml_manager(self) -> None:
        dataset = self.dataset_var.get().strip()
        if not dataset:
            return
        label = self._selected_label if self._selected_label and self._selected_label != "(sin etiqueta)" else None
        if self.open_ml_manager_callback is None:
            messagebox.showinfo("ML Quality Metrics", "No hay integración disponible con ML Manager.")
            return
        self.open_ml_manager_callback(dataset, label)

    def _retrain_dataset(self) -> None:
        dataset = self.dataset_var.get().strip()
        if not dataset:
            return
        if self.retrain_dataset_callback is None:
            messagebox.showinfo("ML Quality Metrics", "No hay integración de reentrenamiento disponible.")
            return
        result = self.retrain_dataset_callback(dataset)
        messagebox.showinfo("ML Quality Metrics", result)

    @staticmethod
    def _fill_text_widget(widget: tk.Text, lines: list[str], empty_message: str = "") -> None:
        widget.delete("1.0", "end")
        if lines:
            widget.insert("1.0", "\n".join(f"- {line}" for line in lines))
        else:
            widget.insert("1.0", empty_message)
