"""Reusable refinement panel for AI-generated content review dialogs."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

from app.services.voice_dictation import VoiceDictationError, VoiceDictationService
from app.ui.dictation_widgets import register_dictation_focus

logger = logging.getLogger(__name__)


def obtener_texto_dictado(text_widget: tk.Text, dictation_snapshot: str) -> str:
    """Return only the text fragment dictated after recording starts."""
    dictated_text = text_widget.get("1.0", "end").strip()
    if dictated_text.startswith(dictation_snapshot):
        dictated_text = dictated_text[len(dictation_snapshot):].strip()
    return dictated_text


class RefinamientoPanel(ttk.LabelFrame):
    """Unified refinement UI and behavior for summaries, attachments, and AI responses."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        texto_base: str,
        quick_actions: dict[str, str],
        on_restore_version: Callable[[str], None],
        max_refinements: int,
    ) -> None:
        super().__init__(parent, text="Refinar resultado")
        self.texto_base = (texto_base or "").strip()
        self.quick_actions = quick_actions
        self.on_restore_version = on_restore_version
        self.max_refinements = max_refinements

        self.refinamientos: list[str] = []
        self.historial: list[dict[str, object]] = []
        self.refinements_used = 0
        self._rendering_chips = False
        self._dictation_snapshot = ""

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        self._build_ui()
        self._build_dictation()

    def _build_ui(self) -> None:
        input_frame = ttk.Frame(self)
        input_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        input_frame.grid_columnconfigure(0, weight=1)
        input_frame.grid_rowconfigure(0, weight=1)

        self.refine_text = tk.Text(input_frame, height=4, wrap="word")
        self.refine_text.grid(row=0, column=0, sticky="nsew")
        refine_scrollbar = ttk.Scrollbar(input_frame, orient="vertical", command=self.refine_text.yview)
        refine_scrollbar.grid(row=0, column=1, sticky="ns")
        self.refine_text.configure(yscrollcommand=refine_scrollbar.set)
        register_dictation_focus(self.refine_text)

        chips_container = ttk.Frame(self)
        chips_container.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        chips_container.grid_columnconfigure(0, weight=1)
        self.chips_frame = ttk.Frame(chips_container)
        self.chips_frame.grid(row=0, column=0, sticky="ew")

        quick_actions_frame = ttk.Frame(self)
        quick_actions_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        for label, instruction in self.quick_actions.items():
            ttk.Button(
                quick_actions_frame,
                text=f"➕ {label}",
                command=lambda value=instruction: self.append_refinement(value),
            ).pack(side="left", padx=(0, 4), pady=(0, 4))

        self.dictation_controls = ttk.Frame(self)
        self.dictation_controls.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))

        history_frame = ttk.LabelFrame(self, text="Historial de refinamiento")
        history_frame.grid(row=5, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.history_list = tk.Listbox(history_frame, height=4, exportselection=False)
        self.history_list.pack(fill="x", padx=6, pady=6)
        self.history_list.bind("<<ListboxSelect>>", lambda _event: self.restore_selected_version())

    def _build_dictation(self) -> None:
        self.mic_state = ttk.Label(self.dictation_controls, text="")
        self.mic_state.pack(side="left", padx=(0, 6))

        try:
            self.dictation_service = VoiceDictationService(
                self,
                status_callback=self._set_dictation_status,
                error_callback=self._show_dictation_error,
            )
        except Exception as e:  # noqa: BLE001
            print("⚠️ Error inicializando dictado:", e)
            self.dictation_service = None

        self.dictation_button = ttk.Button(self.dictation_controls, text="🎤 Dictar", command=self.toggle_refinement_dictation)
        self.dictation_button.pack(side="left")
        if self.dictation_service is None:
            self.dictation_button.configure(state="disabled")
            self._set_dictation_status("Dictado no disponible")
        ttk.Button(self.dictation_controls, text="➕ Añadir instrucciones", command=self.add_manual_refinements).pack(side="left", padx=6)
        ttk.Button(self.dictation_controls, text="🧹 Limpiar instrucciones", command=self.clear_refinements).pack(side="left", padx=6)

    def _set_dictation_status(self, text: str) -> None:
        self.mic_state.configure(text=text)

    def _show_dictation_error(self, msg: str) -> None:
        if msg:
            messagebox.showerror("Dictado", msg)

    def append_refinement(self, instruction: str) -> None:
        self.add_refinement_lines(instruction)

    def add_refinement_lines(self, raw_value: str) -> bool:
        changed = False
        for line in (raw_value or "").splitlines():
            normalized = line.strip()
            if not normalized or normalized in self.refinamientos:
                continue
            self.refinamientos.append(normalized)
            changed = True
        if changed:
            self.actualizar_input()
            self.render_chips()
        return changed

    def actualizar_input(self) -> None:
        self.refine_text.delete("1.0", "end")
        if self.refinamientos:
            self.refine_text.insert("1.0", "\n".join(self.refinamientos))

    def render_chips(self) -> None:
        if self._rendering_chips:
            return
        self._rendering_chips = True
        try:
            for widget in self.chips_frame.winfo_children():
                widget.destroy()
            if not self.refinamientos:
                return

            chips_font = tkfont.nametofont("TkDefaultFont")
            available_width = self.chips_frame.winfo_width() or 600
            row_index = 0
            col_index = 0
            current_width = 0

            for texto in self.refinamientos:
                chip_width = chips_font.measure(texto) + 52
                if col_index > 0 and current_width + chip_width > available_width:
                    row_index += 1
                    col_index = 0
                    current_width = 0

                chip = ttk.Frame(self.chips_frame, style="Chip.TFrame")
                chip.grid(row=row_index, column=col_index, padx=(0, 6), pady=(0, 6), sticky="w")
                ttk.Label(chip, text=texto, style="Chip.TLabel").pack(side="left", padx=(6, 4), pady=2)
                ttk.Button(chip, text="✕", width=2, style="ChipClose.TButton", command=lambda value=texto: self.eliminar_chip(value)).pack(
                    side="left", padx=(0, 4), pady=2
                )
                col_index += 1
                current_width += chip_width + 6
        finally:
            self._rendering_chips = False

    def eliminar_chip(self, texto: str) -> None:
        if texto in self.refinamientos:
            self.refinamientos.remove(texto)
            self.actualizar_input()
            self.render_chips()

    def add_manual_refinements(self) -> None:
        self.add_refinement_lines(self.refine_text.get("1.0", "end"))

    def toggle_refinement_dictation(self) -> None:
        if self.dictation_service is None:
            self._set_dictation_status("Dictado no disponible")
            return

        try:
            if not self.dictation_service.recording:
                self._dictation_snapshot = self.refine_text.get("1.0", "end").strip()
                self.refine_text.focus_set()
                self.dictation_service.toggle_recording()
                self.dictation_button.configure(text="⏹ Detener dictado")
                return
            self.dictation_service.toggle_recording()
            self.dictation_button.configure(text="🎤 Dictar")
        except VoiceDictationError as exc:
            logger.exception("Error en dictado de refinamiento")
            self._show_dictation_error(str(exc))
            self.dictation_button.configure(text="🎤 Dictar")
            return

        dictated_text = obtener_texto_dictado(self.refine_text, self._dictation_snapshot)
        if dictated_text:
            self.add_refinement_lines(dictated_text)

    def clear_refinements(self) -> None:
        self.refinamientos.clear()
        self._dictation_snapshot = ""
        self.actualizar_input()
        self.render_chips()
        self._set_dictation_status("")

    def get_prompt_final(self) -> str:
        return (
            "Refina el siguiente contenido:\n\n"
            f"{self.texto_base}\n\n"
            "Aplicando:\n\n"
            + "\n".join(f"* {instruction}" for instruction in self.refinamientos)
        )

    def can_refine(self) -> bool:
        self.add_manual_refinements()
        if not self.refinamientos:
            messagebox.showwarning("Atención", "Escribe una instrucción para refinar el resultado.")
            return False
        if self.refinements_used >= self.max_refinements:
            messagebox.showinfo("Refinamiento", "Has alcanzado el máximo de refinamientos. Guarda una versión final.")
            return False
        return True

    def record_version(self, resultado: str) -> None:
        self.refinements_used += 1
        self.historial.append(
            {
                "version": len(self.historial) + 1,
                "refinamientos": list(self.refinamientos),
                "resultado": resultado,
            }
        )
        self.refresh_history()

    def seed_history(self, initial_result: str) -> None:
        self.historial = [{"version": 1, "refinamientos": [], "resultado": initial_result}]
        self.refresh_history()

    def refresh_history(self) -> None:
        self.history_list.delete(0, "end")
        for item in self.historial:
            self.history_list.insert("end", f"Versión {item['version']}")
        if self.historial:
            self.history_list.selection_clear(0, "end")
            self.history_list.selection_set(len(self.historial) - 1)

    def restore_selected_version(self) -> None:
        selected = self.history_list.curselection()
        if not selected:
            return
        restored_index = int(selected[0])
        selected_item = self.historial[restored_index]
        resultado = str(selected_item.get("resultado") or "")
        refinamientos = selected_item.get("refinamientos") or []
        self.on_restore_version(resultado)
        self.refinamientos.clear()
        self.refinamientos.extend(str(value) for value in refinamientos)
        self.actualizar_input()
        self.render_chips()
