"""Intermediate metadata dialog for Knowledge Manager note creation."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.persistence.knowledge_repository import KnowledgeRepository
from app.persistence.masters_repository import MastersRepository
from app.ui.app_icons import apply_app_icon


class KnowledgeMetadataDialog(tk.Toplevel):
    """Let users review Knowledge metadata before saving a sourced note."""

    def __init__(
        self,
        parent: tk.Misc,
        db_connection: sqlite3.Connection,
        *,
        title: str,
        content: str,
        source: str,
        suggestions: dict[str, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.result: dict[str, object] | None = None
        self.repo = KnowledgeRepository(db_connection)
        self.masters_repo = MastersRepository(db_connection)
        self._content = content or ""
        self._suggestions = suggestions or {}
        self._topics_by_label: dict[str, int | None] = {"": None}

        self.title("Sansebas Nexus - Clasificar nota")
        apply_app_icon(self)
        self.geometry("760x620")
        self.minsize(640, 520)

        self.title_var = tk.StringVar(value=(title or "").strip())
        self.area_var = tk.StringVar()
        self.topic_var = tk.StringVar()
        self.type_var = tk.StringVar()
        self.tags_var = tk.StringVar(value=", ".join(self._suggested_tags()))
        self.source_var = tk.StringVar(value=(source or "manual").strip() or "manual")
        self.reason_var = tk.StringVar(value=str(self._suggestions.get("reason") or ""))

        self._area_values = self._master_values("Area", fallback=["Archivo"])
        self._type_values = self._master_values("Tipo", fallback=["Nota"])
        self._build_ui()
        self._apply_suggestions()
        self._center(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.title_entry.focus_set()
        self.wait_window(self)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        ttk.Label(root, text="Revisa la clasificación antes de guardar en Knowledge.").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 10),
        )

        ttk.Label(root, text="Título").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.title_entry = ttk.Entry(root, textvariable=self.title_var)
        self.title_entry.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(root, text="Área").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        self.area_combo = ttk.Combobox(root, textvariable=self.area_var, values=self._area_values, state="readonly")
        self.area_combo.grid(row=2, column=1, sticky="ew", pady=4)
        self.area_combo.bind("<<ComboboxSelected>>", self._on_area_changed)

        ttk.Label(root, text="Tema").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        self.topic_combo = ttk.Combobox(root, textvariable=self.topic_var, state="readonly")
        self.topic_combo.grid(row=3, column=1, sticky="ew", pady=4)

        ttk.Label(root, text="Tipo").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        self.type_combo = ttk.Combobox(root, textvariable=self.type_var, values=self._type_values, state="readonly")
        self.type_combo.grid(row=4, column=1, sticky="ew", pady=4)

        ttk.Label(root, text="Etiquetas").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=4)
        tags_entry = ttk.Entry(root, textvariable=self.tags_var)
        tags_entry.grid(row=5, column=1, sticky="ew", pady=4)

        reference_frame = ttk.LabelFrame(root, text="Referencia", padding=8)
        reference_frame.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        reference_frame.columnconfigure(0, weight=1)
        reference_frame.rowconfigure(2, weight=1)

        source_row = ttk.Frame(reference_frame)
        source_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(source_row, text="Fuente:").pack(side="left")
        ttk.Label(source_row, textvariable=self.source_var).pack(side="left", padx=(6, 0))

        if self.reason_var.get():
            reason_label = ttk.Label(
                reference_frame,
                textvariable=self.reason_var,
                foreground="#555555",
                wraplength=680,
            )
            reason_label.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self.preview_text = ScrolledText(reference_frame, height=12, wrap="word")
        self.preview_text.grid(row=2, column=0, sticky="nsew")
        preview = self._content.strip()
        if len(preview) > 4000:
            preview = f"{preview[:4000].rstrip()}\n\n[…]"
        self.preview_text.insert("1.0", preview)
        self.preview_text.configure(state="disabled")

        buttons = ttk.Frame(root)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancelar", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="Guardar", command=self._accept).pack(side="right", padx=(0, 8))

    def _master_values(self, category: str, fallback: list[str]) -> list[str]:
        try:
            values = [value for value in self.masters_repo.list_active(category) if str(value).strip()]
        except Exception:  # noqa: BLE001
            values = []
        return values or fallback

    def _suggested_tags(self) -> list[str]:
        tags = self._suggestions.get("tags") or []
        return [str(tag).strip() for tag in tags if str(tag).strip()]

    @staticmethod
    def _choose_value(suggested: object, values: list[str], fallback: str) -> str:
        suggested_text = str(suggested or "").strip()
        for value in values:
            if value.casefold() == suggested_text.casefold():
                return value
        for value in values:
            if value.casefold() == fallback.casefold():
                return value
        return values[0] if values else fallback

    def _apply_suggestions(self) -> None:
        self.area_var.set(
            self._choose_value(self._suggestions.get("area"), self._area_values, "Archivo")
        )
        self.type_var.set(
            self._choose_value(self._suggestions.get("type"), self._type_values, "Nota")
        )
        self._refresh_topics(keep_value=str(self._suggestions.get("topic") or ""))

    def _refresh_topics(self, keep_value: str = "") -> None:
        area = self.area_var.get().strip()
        rows = self.repo.list_topics(area=area) if area else self.repo.list_topics()
        self._topics_by_label = {"": None}
        for row in rows:
            label = str(row["name"] or "").strip()
            if not label:
                continue
            if not area and row["area_name"]:
                label = f"{row['area_name']} / {label}"
            self._topics_by_label[label] = int(row["id"])
        values = list(self._topics_by_label.keys())
        self.topic_combo.configure(values=values)
        if keep_value in values:
            self.topic_var.set(keep_value)
        else:
            self.topic_var.set("")

    def _on_area_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_topics()

    def _tags_from_entry(self) -> list[str]:
        raw = self.tags_var.get().replace("\n", ",")
        tags: list[str] = []
        seen: set[str] = set()
        for value in raw.split(","):
            cleaned = value.strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                tags.append(cleaned)
                seen.add(key)
        return tags

    def _accept(self) -> None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Sansebas Nexus", "El título es obligatorio.", parent=self)
            return
        self.result = {
            "title": title,
            "content": self._content,
            "area": self.area_var.get().strip(),
            "topic_id": self._topics_by_label.get(self.topic_var.get(), None),
            "topic": self.topic_var.get().strip(),
            "type": self.type_var.get().strip(),
            "tags": self._tags_from_entry(),
            "source": self.source_var.get().strip() or "manual",
        }
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _center(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        try:
            x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
            y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 2, 0)
        except tk.TclError:
            x = max((self.winfo_screenwidth() - width) // 2, 0)
            y = max((self.winfo_screenheight() - height) // 2, 0)
        self.geometry(f"+{x}+{y}")
