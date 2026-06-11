"""Intermediate metadata dialog for Knowledge Manager note creation."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from pathlib import Path
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
        attachments: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.result: dict[str, object] | None = None
        self.repo = KnowledgeRepository(db_connection)
        self.masters_repo = MastersRepository(db_connection)
        self._content = content or ""
        self._suggestions = suggestions or {}
        self._attachments = self._normalize_attachments(attachments or [])
        self._attachment_vars: list[tk.BooleanVar] = []
        self._attachment_tree_iids: dict[str, int] = {}
        self._topics_by_label: dict[str, int | None] = {"": None}

        self.title("Sansebas Nexus - Clasificar nota")
        apply_app_icon(self)
        self.geometry("860x720")
        self.minsize(720, 560)

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

    @staticmethod
    def _attachment_filename(attachment: dict[str, object]) -> str:
        return str(attachment.get("filename") or attachment.get("name") or "adjunto").strip() or "adjunto"

    @staticmethod
    def _attachment_mime(attachment: dict[str, object]) -> str:
        return str(
            attachment.get("mime")
            or attachment.get("mime_type")
            or attachment.get("mimeType")
            or "application/octet-stream"
        ).strip()

    @staticmethod
    def _attachment_size(attachment: dict[str, object]) -> int:
        raw_size = attachment.get("size") or attachment.get("file_size") or 0
        try:
            return int(raw_size or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _format_attachment_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} bytes"

    @classmethod
    def _should_preselect_attachment(cls, attachment: dict[str, object]) -> bool:
        filename = cls._attachment_filename(attachment)
        suffix = Path(filename).suffix.lower().lstrip(".")
        mime_type = cls._attachment_mime(attachment).lower()
        size = cls._attachment_size(attachment)
        useful_extensions = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "zip"}
        image_extensions = {"png", "jpg", "jpeg", "gif", "webp"}
        corporate_markers = (
            "logo",
            "firma",
            "signature",
            "facebook",
            "linkedin",
            "twitter",
            "instagram",
            "icon",
            "banner",
        )

        if suffix in useful_extensions:
            return True
        is_image = suffix in image_extensions or mime_type.startswith("image/")
        if is_image:
            normalized_name = filename.casefold()
            looks_corporate = any(marker in normalized_name for marker in corporate_markers)
            if looks_corporate or (0 < size < 50 * 1024):
                return False
            return True
        return True

    @classmethod
    def _normalize_attachments(cls, attachments: list[dict[str, object]]) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                continue
            item = dict(attachment)
            item.setdefault("filename", cls._attachment_filename(item))
            item.setdefault("mime", cls._attachment_mime(item))
            item.setdefault("size", cls._attachment_size(item))
            item["_dialog_index"] = index
            normalized.append(item)
        return normalized

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)
        root.rowconfigure(7, weight=0)

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

        self._build_attachments_section(root)

        buttons = ttk.Frame(root)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancelar", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="Guardar", command=self._accept).pack(side="right", padx=(0, 8))

    def _build_attachments_section(self, root: ttk.Frame) -> None:
        if not self._attachments:
            return

        attachments_frame = ttk.LabelFrame(root, text="Adjuntos del email", padding=8)
        attachments_frame.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        attachments_frame.columnconfigure(0, weight=1)
        attachments_frame.rowconfigure(0, weight=1)

        columns = ("save", "filename", "mime", "size")
        self.attachments_tree = ttk.Treeview(attachments_frame, columns=columns, show="headings", height=5)
        self.attachments_tree.heading("save", text="Guardar")
        self.attachments_tree.heading("filename", text="Archivo")
        self.attachments_tree.heading("mime", text="Tipo")
        self.attachments_tree.heading("size", text="Tamaño")
        self.attachments_tree.column("save", width=80, minwidth=70, stretch=False, anchor="center")
        self.attachments_tree.column("filename", width=300, minwidth=180, stretch=True)
        self.attachments_tree.column("mime", width=220, minwidth=130, stretch=True)
        self.attachments_tree.column("size", width=100, minwidth=80, stretch=False, anchor="e")
        self.attachments_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(attachments_frame, orient="vertical", command=self.attachments_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.attachments_tree.configure(yscrollcommand=scrollbar.set)
        self.attachments_tree.bind("<Button-1>", self._on_attachment_tree_click)
        self.attachments_tree.bind("<space>", self._toggle_focused_attachment)

        for index, attachment in enumerate(self._attachments):
            selected = self._should_preselect_attachment(attachment)
            var = tk.BooleanVar(value=selected)
            self._attachment_vars.append(var)
            iid = str(index)
            self._attachment_tree_iids[iid] = index
            self.attachments_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "☑" if selected else "☐",
                    self._attachment_filename(attachment),
                    self._attachment_mime(attachment),
                    self._format_attachment_size(self._attachment_size(attachment)),
                ),
            )

        quick_buttons = ttk.Frame(attachments_frame)
        quick_buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            quick_buttons,
            text="Seleccionar todos",
            command=lambda: self._set_all_attachments(True),
        ).pack(side="left")
        ttk.Button(
            quick_buttons,
            text="Deseleccionar todos",
            command=lambda: self._set_all_attachments(False),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            quick_buttons,
            text="Deseleccionar imágenes pequeñas",
            command=self._deselect_small_images,
        ).pack(side="left", padx=(6, 0))

    def _refresh_attachment_row(self, index: int) -> None:
        if not hasattr(self, "attachments_tree"):
            return
        attachment = self._attachments[index]
        self.attachments_tree.item(
            str(index),
            values=(
                "☑" if self._attachment_vars[index].get() else "☐",
                self._attachment_filename(attachment),
                self._attachment_mime(attachment),
                self._format_attachment_size(self._attachment_size(attachment)),
            ),
        )

    def _set_attachment_selected(self, index: int, selected: bool) -> None:
        self._attachment_vars[index].set(selected)
        self._refresh_attachment_row(index)

    def _set_all_attachments(self, selected: bool) -> None:
        for index in range(len(self._attachment_vars)):
            self._set_attachment_selected(index, selected)

    def _is_small_image_attachment(self, attachment: dict[str, object]) -> bool:
        filename = self._attachment_filename(attachment)
        suffix = Path(filename).suffix.lower().lstrip(".")
        mime_type = self._attachment_mime(attachment).lower()
        size = self._attachment_size(attachment)
        return (
            suffix in {"png", "jpg", "jpeg", "gif", "webp"} or mime_type.startswith("image/")
        ) and size < 50 * 1024

    def _deselect_small_images(self) -> None:
        for index, attachment in enumerate(self._attachments):
            if self._is_small_image_attachment(attachment):
                self._set_attachment_selected(index, False)

    def _toggle_attachment(self, index: int) -> None:
        self._set_attachment_selected(index, not self._attachment_vars[index].get())

    def _on_attachment_tree_click(self, event: tk.Event) -> None:
        if not hasattr(self, "attachments_tree"):
            return
        region = self.attachments_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = self.attachments_tree.identify_column(event.x)
        if column != "#1":
            return
        row_id = self.attachments_tree.identify_row(event.y)
        if not row_id:
            return
        index = self._attachment_tree_iids.get(row_id)
        if index is not None:
            self._toggle_attachment(index)

    def _toggle_focused_attachment(self, _event: tk.Event | None = None) -> str:
        if not hasattr(self, "attachments_tree"):
            return "break"
        focused = self.attachments_tree.focus()
        index = self._attachment_tree_iids.get(str(focused))
        if index is not None:
            self._toggle_attachment(index)
        return "break"

    def _selected_attachments(self) -> list[dict[str, object]]:
        selected: list[dict[str, object]] = []
        for index, attachment in enumerate(self._attachments):
            if index < len(self._attachment_vars) and self._attachment_vars[index].get():
                selected.append(attachment)
        return selected

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
            "attachments": self._selected_attachments(),
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
