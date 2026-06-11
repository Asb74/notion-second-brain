"""Pre-import dialog for pilot Evernote ENEX imports into Knowledge."""

from __future__ import annotations

import logging
import mimetypes
import re
import sqlite3
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from tkinter import messagebox, ttk

from app.config.config_paths import knowledge_attachments_dir
from app.persistence.knowledge_repository import KnowledgeRepository
from app.persistence.masters_repository import MastersRepository
from app.services.knowledge_suggestion_service import suggest_knowledge_metadata
from app.ui.app_icons import apply_app_icon

logger = logging.getLogger(__name__)

TAG_MODE_KEEP_ADD = "Añadir etiquetas sugeridas"
TAG_MODE_KEEP = "Conservar etiquetas Evernote"
TAG_MODE_REPLACE = "Sustituir por etiquetas sugeridas"
TAG_MODE_NONE = "Importar sin etiquetas"
TOPIC_MODE_NOTEBOOK = "Usar libreta Evernote como tema si existe"
TOPIC_MODE_FIXED = "Usar tema fijo seleccionado"
TOPIC_MODE_AUTO = "Sugerir automáticamente"


class EvernoteImportDialog(tk.Toplevel):
    """Show detected ENEX notes and import the selected subset into Knowledge."""

    def __init__(
        self,
        parent: tk.Misc,
        db_connection: sqlite3.Connection,
        enex_path: str | Path,
        notes: list[dict[str, object]],
        on_import_finished: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.repo = KnowledgeRepository(db_connection)
        self.masters_repo = MastersRepository(db_connection)
        self.enex_path = Path(enex_path)
        self.notes = notes
        self.on_import_finished = on_import_finished
        self.selected_vars: list[tk.BooleanVar] = []
        self.tree_iids: dict[str, int] = {}
        self.area_var = tk.StringVar()
        self.topic_mode_var = tk.StringVar(value=TOPIC_MODE_NOTEBOOK)
        self.fixed_topic_var = tk.StringVar()
        self.type_var = tk.StringVar()
        self.tag_mode_var = tk.StringVar(value=TAG_MODE_KEEP_ADD)
        self.status_var = tk.StringVar(value="Listo")
        self._importing = False
        self._import_options: dict[str, str] = {}

        self.title("Importar Evernote (.enex) a Knowledge")
        apply_app_icon(self)
        self.geometry("1120x680")
        self.minsize(940, 560)
        self.transient(parent)

        self._build_layout()
        self._load_reference_values()
        self._populate_notes()
        self.grab_set()
        self.focus_force()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, padding=10)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Archivo:").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=str(self.enex_path), wraplength=880).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(header, text="Notas detectadas:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, text=str(len(self.notes))).grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))

        options = ttk.LabelFrame(self, text="Clasificación", padding=10)
        options.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        for col in (1, 3):
            options.columnconfigure(col, weight=1)
        ttk.Label(options, text="Área por defecto").grid(row=0, column=0, sticky="w")
        self.area_combo = ttk.Combobox(options, textvariable=self.area_var, state="readonly")
        self.area_combo.grid(row=0, column=1, sticky="ew", padx=(6, 14), pady=2)
        ttk.Label(options, text="Tipo").grid(row=0, column=2, sticky="w")
        self.type_combo = ttk.Combobox(options, textvariable=self.type_var, state="readonly")
        self.type_combo.grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=2)

        ttk.Label(options, text="Tema").grid(row=1, column=0, sticky="w")
        self.topic_mode_combo = ttk.Combobox(
            options,
            textvariable=self.topic_mode_var,
            values=[TOPIC_MODE_NOTEBOOK, TOPIC_MODE_FIXED, TOPIC_MODE_AUTO],
            state="readonly",
        )
        self.topic_mode_combo.grid(row=1, column=1, sticky="ew", padx=(6, 14), pady=2)
        ttk.Label(options, text="Tema fijo").grid(row=1, column=2, sticky="w")
        self.fixed_topic_combo = ttk.Combobox(options, textvariable=self.fixed_topic_var, state="readonly")
        self.fixed_topic_combo.grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=2)

        ttk.Label(options, text="Etiquetas").grid(row=2, column=0, sticky="w")
        self.tag_mode_combo = ttk.Combobox(
            options,
            textvariable=self.tag_mode_var,
            values=[TAG_MODE_KEEP_ADD, TAG_MODE_KEEP, TAG_MODE_REPLACE, TAG_MODE_NONE],
            state="readonly",
        )
        self.tag_mode_combo.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=2)

        list_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        columns = ("import", "title", "date", "tags", "attachments")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "import": "Importar",
            "title": "Título",
            "date": "Fecha",
            "tags": "Etiquetas originales",
            "attachments": "Adjuntos",
        }
        widths = {"import": 80, "title": 360, "date": 150, "tags": 340, "attachments": 80}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                stretch=column in {"title", "tags"},
                anchor="center" if column in {"import", "attachments"} else "w",
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._toggle_focused_note)

        footer = ttk.Frame(self, padding=10)
        footer.grid(row=3, column=0, sticky="ew")
        ttk.Button(footer, text="Seleccionar primeras 10", command=lambda: self._select_first(10)).pack(side="left", padx=(0, 6))
        ttk.Button(footer, text="Seleccionar todas", command=lambda: self._set_all(True)).pack(side="left", padx=(0, 6))
        ttk.Button(footer, text="Deseleccionar todas", command=lambda: self._set_all(False)).pack(side="left", padx=(0, 16))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Importar seleccionadas", command=self.import_selected).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="Cancelar", command=self.destroy).pack(side="right")

    def _load_reference_values(self) -> None:
        areas = self.masters_repo.list_active("Area")
        types = self.masters_repo.list_active("Tipo")
        topics = self._topic_values()
        self.area_combo.configure(values=areas)
        self.type_combo.configure(values=types)
        self.fixed_topic_combo.configure(values=["", *topics])
        self.area_var.set("Archivo" if "Archivo" in areas else (areas[0] if areas else ""))
        self.type_var.set("Nota" if "Nota" in types else ("Documento" if "Documento" in types else (types[0] if types else "")))
        self.fixed_topic_var.set(topics[0] if topics else "")

    def _topic_values(self) -> list[str]:
        rows = self.repo.list_topics(active_only=True)
        values: list[str] = []
        for row in rows:
            area = str(row["area_name"] or "")
            name = str(row["name"] or "")
            values.append(f"{area} / {name}" if area else name)
        return values

    def _populate_notes(self) -> None:
        for index, note in enumerate(self.notes):
            var = tk.BooleanVar(value=index < 10)
            self.selected_vars.append(var)
            iid = f"note:{index}"
            self.tree_iids[iid] = index
            self.tree.insert("", "end", iid=iid, values=self._row_values(index))
        self._update_status()

    def _row_values(self, index: int) -> tuple[str, str, str, str, str]:
        note = self.notes[index]
        return (
            "☑" if self.selected_vars[index].get() else "☐",
            str(note.get("title") or "Nota sin título"),
            str(note.get("created") or note.get("updated") or ""),
            ", ".join(str(tag) for tag in note.get("tags") or []),
            str(len(note.get("resources") or [])),
        )

    def _refresh_row(self, index: int) -> None:
        iid = f"note:{index}"
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(index))
        self._update_status()

    def _set_selected(self, index: int, selected: bool) -> None:
        self.selected_vars[index].set(selected)
        self._refresh_row(index)

    def _set_all(self, selected: bool) -> None:
        for index in range(len(self.selected_vars)):
            self.selected_vars[index].set(selected)
            self.tree.item(f"note:{index}", values=self._row_values(index))
        self._update_status()

    def _select_first(self, limit: int) -> None:
        for index in range(len(self.selected_vars)):
            self.selected_vars[index].set(index < limit)
            self.tree.item(f"note:{index}", values=self._row_values(index))
        self._update_status()

    def _update_status(self) -> None:
        selected = sum(1 for var in self.selected_vars if var.get())
        self.status_var.set(f"{selected} de {len(self.notes)} notas seleccionadas")

    def _on_tree_click(self, event: tk.Event) -> None:
        if self.tree.identify("region", event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return
        row_id = self.tree.identify_row(event.y)
        index = self.tree_iids.get(str(row_id))
        if index is not None:
            self._set_selected(index, not self.selected_vars[index].get())

    def _toggle_focused_note(self, _event: tk.Event | None = None) -> str:
        focused = str(self.tree.focus())
        index = self.tree_iids.get(focused)
        if index is not None:
            self._set_selected(index, not self.selected_vars[index].get())
        return "break"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).name).strip(" ._")
        return cleaned or "adjunto"

    @staticmethod
    def _normalize_tag(tag: object) -> str:
        return re.sub(r"\s+", " ", str(tag or "").strip()).title()

    def _normalize_tags(self, tags: list[object], limit: int = 10) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            normalized = self._normalize_tag(tag)
            key = normalized.casefold()
            if normalized and key not in seen:
                result.append(normalized)
                seen.add(key)
            if len(result) >= limit:
                break
        return result

    def _resolve_tags(self, note: dict[str, object], suggestions: dict[str, object], tag_mode: str) -> list[str]:
        evernote_tags = self._normalize_tags(list(note.get("tags") or []), limit=10)
        suggested_tags = self._normalize_tags(list(suggestions.get("tags") or []), limit=10)
        mode = tag_mode
        if mode == TAG_MODE_NONE:
            return []
        if mode == TAG_MODE_REPLACE:
            return suggested_tags[:10]
        if mode == TAG_MODE_KEEP:
            return evernote_tags[:10]
        return self._normalize_tags([*evernote_tags, *suggested_tags], limit=10)

    def _find_topic_id(self, area: str, topic_name: str) -> int | None:
        cleaned = topic_name.strip()
        if not cleaned:
            return None
        for row in self.repo.list_topics(area=area, active_only=True):
            if str(row["name"] or "").casefold() == cleaned.casefold():
                return int(row["id"])
        return self.repo.create_topic(cleaned, area=area)

    def _fixed_topic_id(self, area: str, fixed_topic: str) -> int | None:
        value = fixed_topic.strip()
        if not value:
            return None
        topic_name = value.split(" / ", 1)[-1].strip()
        return self._find_topic_id(area, topic_name)

    def _resolve_topic_id(
        self,
        note: dict[str, object],
        suggestions: dict[str, object],
        area: str,
        topic_mode: str,
        fixed_topic: str,
    ) -> int | None:
        mode = topic_mode
        if mode == TOPIC_MODE_FIXED:
            return self._fixed_topic_id(area, fixed_topic)
        if mode == TOPIC_MODE_NOTEBOOK:
            notebook = str(note.get("notebook") or "").strip()
            return self._find_topic_id(area, notebook) if notebook else None
        suggested_topic = str(suggestions.get("topic") or "").strip()
        return self._find_topic_id(area, suggested_topic) if suggested_topic else None

    def _summary_for_note(self, note: dict[str, object]) -> str:
        text = str(note.get("content_text") or "").strip()
        return text[:240]

    def _content_for_note(self, note: dict[str, object]) -> str:
        content = str(note.get("content_text") or "").strip()
        metadata: list[str] = []
        if note.get("created"):
            metadata.append(f"Fecha original Evernote creación: {note.get('created')}")
        if note.get("updated"):
            metadata.append(f"Fecha original Evernote actualización: {note.get('updated')}")
        if note.get("notebook"):
            metadata.append(f"Libreta Evernote: {note.get('notebook')}")
        return "\n\n".join([part for part in [content, "\n".join(metadata)] if part])

    def _attachment_item_dir(self, item_id: int) -> Path:
        now = datetime.now()
        return knowledge_attachments_dir() / f"{now:%Y}" / f"{now:%m}" / str(item_id)

    def _unique_path(self, directory: Path, filename: str) -> Path:
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        counter = 1
        while True:
            candidate = directory / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _save_resources(self, item_id: int, resources: list[dict[str, object]]) -> int:
        if not resources:
            return 0
        target_dir = self._attachment_item_dir(item_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        imported = 0
        for resource in resources:
            filename = self._safe_filename(str(resource.get("filename") or "adjunto"))
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            destination = self._unique_path(target_dir, f"{timestamp}_{filename}")
            data = resource.get("data") or b""
            if not isinstance(data, bytes):
                data = bytes(data)
            try:
                destination.write_bytes(data)
                mime_type = str(resource.get("mime") or mimetypes.guess_type(str(destination))[0] or "")
                self.repo.add_attachment(
                    item_id=item_id,
                    original_filename=filename,
                    stored_filename=destination.name,
                    stored_path=str(destination),
                    mime_type=mime_type,
                    file_size=int(resource.get("size") or len(data)),
                    source_type="evernote",
                )
                imported += 1
                logger.info("EVERNOTE_IMPORT: adjunto importado filename=%s", filename)
            except Exception as exc:  # noqa: BLE001
                logger.info("EVERNOTE_IMPORT: error title=%s reason=adjunto %s", item_id, exc)
        return imported

    def import_selected(self) -> None:
        if self._importing:
            return
        selected_indexes = [index for index, var in enumerate(self.selected_vars) if var.get()]
        if not selected_indexes:
            messagebox.showwarning("Importar Evernote", "Selecciona al menos una nota.", parent=self)
            return
        options = {
            "area": self.area_var.get().strip(),
            "tipo": self.type_var.get().strip(),
            "topic_mode": self.topic_mode_var.get().strip(),
            "fixed_topic": self.fixed_topic_var.get().strip(),
            "tag_mode": self.tag_mode_var.get().strip(),
        }
        self._importing = True
        self._import_options = options
        self.status_var.set("Importando notas seleccionadas...")
        self.configure(cursor="watch")
        threading.Thread(target=self._import_worker, args=(selected_indexes, options), daemon=True).start()

    def _import_worker(self, selected_indexes: list[int], options: dict[str, str]) -> None:
        result = {"imported": 0, "skipped": 0, "duplicates": 0, "attachments": 0, "errors": 0}
        for index in selected_indexes:
            note = self.notes[index]
            title = str(note.get("title") or "Nota sin título").strip() or "Nota sin título"
            created = str(note.get("created") or "").strip()
            try:
                if self.repo.exists_evernote_duplicate(title, created):
                    result["duplicates"] += 1
                    result["skipped"] += 1
                    logger.info("EVERNOTE_IMPORT: duplicada title=%s", title)
                    continue
                suggestions = suggest_knowledge_metadata(title, str(note.get("content_text") or ""), source="evernote")
                area = options.get("area", "").strip() or str(suggestions.get("area") or "Archivo")
                tipo = options.get("tipo", "").strip() or str(suggestions.get("type") or "Nota")
                topic_id = self._resolve_topic_id(
                    note,
                    suggestions,
                    area,
                    options.get("topic_mode", TOPIC_MODE_NOTEBOOK),
                    options.get("fixed_topic", ""),
                )
                tags = self._resolve_tags(note, suggestions, options.get("tag_mode", TAG_MODE_KEEP_ADD))
                item_id = self.repo.create_item(
                    title=title,
                    content=self._content_for_note(note),
                    area=area,
                    tipo=tipo,
                    topic_id=topic_id,
                    tags=tags,
                    source_type="evernote",
                    source_id=created,
                    source_path=str(self.enex_path),
                    summary=self._summary_for_note(note),
                )
                result["attachments"] += self._save_resources(item_id, list(note.get("resources") or []))
                result["imported"] += 1
                logger.info("EVERNOTE_IMPORT: nota importada title=%s", title)
            except Exception as exc:  # noqa: BLE001
                result["errors"] += 1
                logger.info("EVERNOTE_IMPORT: error title=%s reason=%s", title, exc)
        try:
            self.after(0, self._finish_import, result)
        except tk.TclError:
            logger.info("EVERNOTE_IMPORT: ventana cerrada antes de mostrar resultado")

    def _finish_import(self, result: dict[str, int]) -> None:
        self._importing = False
        self.configure(cursor="")
        message = (
            "Importación finalizada: "
            f"{result['imported']} notas importadas, "
            f"{result['skipped']} omitidas, "
            f"{result['duplicates']} duplicadas, "
            f"{result['errors']} errores, "
            f"{result['attachments']} adjuntos."
        )
        self.status_var.set(message)
        messagebox.showinfo("Importar Evernote", message, parent=self)
        if self.on_import_finished is not None:
            self.on_import_finished()
