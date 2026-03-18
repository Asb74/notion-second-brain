"""Reusable refinement panel for AI-generated content review dialogs."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

from app.services.voice_dictation import VoiceDictationError, VoiceDictationService
from app.ui.dictation_widgets import register_dictation_focus, safe_configure

logger = logging.getLogger(__name__)

REFINEMENT_MODE_RESPONSE = "response"
REFINEMENT_MODE_EMAIL_SUMMARY = "email_summary"
REFINEMENT_MODE_ATTACHMENT_SUMMARY = "attachment_summary"
OUTPUT_FORMAT_TABLE = "table"
OUTPUT_FORMAT_PARAGRAPH = "paragraph"
OUTPUT_FORMAT_BULLETS = "bullets"
OUTPUT_FORMAT_NUMBERED = "numbered"
OUTPUT_FORMAT_SENTENCES = "sentences"
OUTPUT_FORMAT_PEDIDO = "pedido"
OUTPUT_FORMAT_DEFAULT = OUTPUT_FORMAT_PARAGRAPH
OUTPUT_FORMAT_LABELS: dict[str, str] = {
    OUTPUT_FORMAT_TABLE: "Tabla",
    OUTPUT_FORMAT_PARAGRAPH: "Párrafo",
    OUTPUT_FORMAT_BULLETS: "Viñetas",
    OUTPUT_FORMAT_NUMBERED: "Numerado",
    OUTPUT_FORMAT_SENTENCES: "Frases cortas",
    OUTPUT_FORMAT_PEDIDO: "Pedido",
}

OUTPUT_FORMAT_TAGS: dict[str, str] = {
    OUTPUT_FORMAT_TABLE: "formato tabla",
    OUTPUT_FORMAT_PARAGRAPH: "formato párrafo",
    OUTPUT_FORMAT_BULLETS: "formato viñetas",
    OUTPUT_FORMAT_NUMBERED: "formato numerado",
    OUTPUT_FORMAT_SENTENCES: "formato frases cortas",
    OUTPUT_FORMAT_PEDIDO: "formato pedido",
}

OUTPUT_FORMAT_PROMPTS: dict[str, str] = {
    OUTPUT_FORMAT_TABLE: "Devuelve el resultado en formato tabla.",
    OUTPUT_FORMAT_PARAGRAPH: "Devuelve el resultado en texto continuo en párrafos. No utilices tablas ni listas.",
    OUTPUT_FORMAT_BULLETS: "Devuelve el resultado como lista de viñetas. No utilices tablas.",
    OUTPUT_FORMAT_NUMBERED: "Devuelve el resultado como lista numerada.",
    OUTPUT_FORMAT_SENTENCES: "Devuelve el resultado como frases cortas independientes. Una idea por línea.",
    OUTPUT_FORMAT_PEDIDO: (
        "Devuelve SOLO JSON válido para pedidos. "
        "Estructura esperada: {'Pedidos':[{'PedidoID':'','Cliente':'','Comercial':'','Lineas':[...]}]}. "
        "Cada línea debe incluir: Linea, Palets, NombrePalet, TCajas, CP, NombreCaja, Mercancia, Confeccion, "
        "Calibre, Categoria, Marca, PO, Lote, Observaciones, Cliente, Comercial, FCarga, Plataforma, Pais, PCarga, Estado."
    ),
}
EMAIL_RESPONSE_PARAGRAPH_RULE = (
    "IMPORTANTE: Devuelve el resultado en texto tipo email (párrafos). "
    "Está PROHIBIDO usar tablas o formatos estructurados tipo tabla."
)

REFINEMENT_MODES = {
    REFINEMENT_MODE_RESPONSE,
    REFINEMENT_MODE_EMAIL_SUMMARY,
    REFINEMENT_MODE_ATTACHMENT_SUMMARY,
}


def detect_format(text: str) -> str:
    normalized_text = (text or "").strip()
    if "|" in normalized_text and "\n" in normalized_text:
        return OUTPUT_FORMAT_TABLE
    if normalized_text.startswith("-") or "\n-" in normalized_text:
        return OUTPUT_FORMAT_BULLETS
    if normalized_text.startswith("1.") or "\n1." in normalized_text:
        return OUTPUT_FORMAT_NUMBERED
    return OUTPUT_FORMAT_PARAGRAPH


def get_quick_refinements(refinement_mode: str) -> list[str]:
    quick_refinements = {
        REFINEMENT_MODE_RESPONSE: [
            "más formal",
            "más breve",
            "más cordial",
            "más directa",
            "incluir agradecimiento",
            "orientada a cierre",
        ],
        REFINEMENT_MODE_EMAIL_SUMMARY: [
            "más breve",
            "más detallado",
            "formato tabla",
            "incluir datos numéricos",
            "orientado a acción",
        ],
        REFINEMENT_MODE_ATTACHMENT_SUMMARY: [
            "más breve",
            "más detallado",
            "formato tabla",
            "formato pedido",
            "incluir datos numéricos",
            "extraer campos clave",
            "orientado a acción",
        ],
    }
    normalized_mode = (refinement_mode or "").strip()
    return list(quick_refinements.get(normalized_mode, quick_refinements[REFINEMENT_MODE_EMAIL_SUMMARY]))


def build_refinement_prompt(
    base_text: str,
    refinements: list[str],
    refinement_mode: str,
    original_context: str | None = None,
    output_format: str = OUTPUT_FORMAT_DEFAULT,
) -> str:
    normalized_mode = (refinement_mode or "").strip()
    normalized_output_format = (output_format or "").strip().lower()
    if normalized_mode == REFINEMENT_MODE_RESPONSE:
        normalized_output_format = OUTPUT_FORMAT_PARAGRAPH
    context = (original_context or "").strip() or "No disponible"
    current_text = (base_text or "").strip()
    instruction_lines = [f"- {line.strip()}" for line in refinements if line and line.strip()]
    instructions = "\n".join(instruction_lines) if instruction_lines else "- Sin instrucciones adicionales"

    mode_prompts = {
        REFINEMENT_MODE_RESPONSE: (
            "Estás refinando una respuesta de email.\n"
            "Tu prioridad es producir una respuesta final lista para enviar.\n"
            "Las instrucciones del usuario deben interpretarse como criterios para rehacer, ajustar o mejorar la respuesta.\n"
            "Mantén tono profesional, claridad y utilidad.",
            "RESPUESTA ACTUAL",
            "Reescribe la respuesta teniendo en cuenta las instrucciones.\n"
            "Devuelve solo la respuesta final.",
        ),
        REFINEMENT_MODE_EMAIL_SUMMARY: (
            "Estás refinando un resumen de email.\n"
            "Tu prioridad es producir un resumen útil, fiel y claro.\n"
            "Las instrucciones del usuario deben interpretarse como criterios para resumir, priorizar, extraer o reformatear información.\n"
            "No respondas al email; solo resume.",
            "RESUMEN ACTUAL",
            "Refina el resumen teniendo en cuenta las instrucciones.\n"
            "Devuelve solo el resumen final.",
        ),
        REFINEMENT_MODE_ATTACHMENT_SUMMARY: (
            "Estás refinando un resumen de adjuntos o una extracción documental.\n"
            "Tu prioridad es producir un resumen útil para trabajo operativo.\n"
            "Las instrucciones del usuario deben interpretarse como criterios para extraer datos, priorizar campos, resumir o reformatear información.\n"
            "No respondas al email; céntrate en el contenido documental.",
            "RESUMEN ACTUAL",
            "Refina el resumen de adjuntos teniendo en cuenta las instrucciones.\n"
            "Si el usuario pide campos concretos, extráelos explícitamente.\n"
            "Devuelve solo el resultado final.",
        ),
    }

    base_instruction, current_text_title, task_instruction = mode_prompts.get(
        normalized_mode,
        mode_prompts[REFINEMENT_MODE_EMAIL_SUMMARY],
    )

    format_instruction = OUTPUT_FORMAT_PROMPTS.get(normalized_output_format, OUTPUT_FORMAT_PROMPTS[OUTPUT_FORMAT_DEFAULT])
    if normalized_mode == REFINEMENT_MODE_RESPONSE:
        format_instruction = f"{format_instruction}\n{EMAIL_RESPONSE_PARAGRAPH_RULE}"

    return (
        f"{base_instruction}\n\n"
        "CONTEXTO ORIGINAL\n"
        f"{context}\n\n"
        f"{current_text_title}\n"
        f"{current_text}\n\n"
        "INSTRUCCIONES DEL USUARIO\n"
        f"{instructions}\n\n"
        "TAREA\n"
        f"{task_instruction}\n\n"
        "FORMATO DE SALIDA (OBLIGATORIO):\n"
        f"{format_instruction}"
    )


def obtener_texto_dictado(text_widget: tk.Text, dictation_snapshot: str) -> str:
    """Return only the text fragment dictated after recording starts."""
    if not text_widget or not text_widget.winfo_exists():
        return ""
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
        refinement_mode: str,
        original_context: str | None,
        on_restore_version: Callable[[str], None],
        max_refinements: int,
    ) -> None:
        super().__init__(parent, text="Refinar resultado")
        self.texto_base = (texto_base or "").strip()
        self.refinement_mode = (refinement_mode or "").strip()
        if self.refinement_mode not in REFINEMENT_MODES:
            self.refinement_mode = REFINEMENT_MODE_EMAIL_SUMMARY
        self.original_context = original_context
        self.on_restore_version = on_restore_version
        self.max_refinements = max_refinements

        self.refinamientos: list[str] = []
        self.output_format = OUTPUT_FORMAT_DEFAULT
        self.formato_seleccionado = OUTPUT_FORMAT_DEFAULT
        self.requested_output_format = OUTPUT_FORMAT_DEFAULT
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
        for label in get_quick_refinements(self.refinement_mode):
            ttk.Button(
                quick_actions_frame,
                text=f"➕ {label}",
                command=lambda value=label: self.append_refinement(value),
            ).pack(side="left", padx=(0, 4), pady=(0, 4))

        format_frame = ttk.Frame(self)
        format_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        if self._supports_output_format():
            self.format_selector_var = tk.StringVar(value=OUTPUT_FORMAT_DEFAULT)
            self.format_menu_button = ttk.Menubutton(format_frame, text="")
            format_menu = tk.Menu(self.format_menu_button, tearoff=0)
            for output_format in OUTPUT_FORMAT_TAGS:
                format_menu.add_radiobutton(
                    label=OUTPUT_FORMAT_LABELS.get(output_format, output_format.title()),
                    value=output_format,
                    variable=self.format_selector_var,
                    command=lambda value=output_format: self.set_output_format(value),
                )
            self.format_menu_button.configure(menu=format_menu)
            self.format_menu_button.pack(side="left")
        self._update_format_button_label()
        self.set_output_format(OUTPUT_FORMAT_DEFAULT)

        self.dictation_controls = ttk.Frame(self)
        self.dictation_controls.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 4))

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
        try:
            safe_configure(self.mic_state, text=text)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo actualizar estado de dictado de refinamiento", exc_info=True)

    def _show_dictation_error(self, msg: str) -> None:
        if not msg or not self.winfo_exists():
            return
        try:
            messagebox.showerror("Dictado", msg)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo mostrar error de dictado de refinamiento", exc_info=True)

    def append_refinement(self, instruction: str) -> None:
        self.add_refinement_lines(instruction)

    def _supports_output_format(self) -> bool:
        return self.refinement_mode in {REFINEMENT_MODE_EMAIL_SUMMARY, REFINEMENT_MODE_ATTACHMENT_SUMMARY}

    def _update_current_format_indicator(self) -> None:
        self.update_ui_format(self.output_format)

    def _update_format_button_label(self) -> None:
        if not hasattr(self, "format_menu_button"):
            return
        selected = (self.formato_seleccionado or OUTPUT_FORMAT_DEFAULT).strip().lower()
        display_label = OUTPUT_FORMAT_LABELS.get(selected, selected.title())
        self.format_menu_button.configure(text=f"+ {display_label}")

    def update_ui_format(self, output_format: str) -> None:
        normalized_format = (output_format or "").strip().lower()
        if normalized_format not in OUTPUT_FORMAT_TAGS:
            normalized_format = OUTPUT_FORMAT_DEFAULT
        self.output_format = normalized_format
        self.formato_seleccionado = normalized_format
        if hasattr(self, "format_selector_var"):
            self.format_selector_var.set(self.formato_seleccionado)
        self._update_format_button_label()

    def _remove_existing_format_tags(self) -> None:
        format_tags = set(OUTPUT_FORMAT_TAGS.values())
        self.refinamientos = [item for item in self.refinamientos if item.lower() not in format_tags]

    def set_output_format(self, output_format: str) -> None:
        normalized_format = (output_format or "").strip().lower()
        if normalized_format not in OUTPUT_FORMAT_TAGS:
            normalized_format = OUTPUT_FORMAT_DEFAULT
        self.formato_seleccionado = normalized_format
        self.requested_output_format = normalized_format
        self.output_format = self.formato_seleccionado
        self._remove_existing_format_tags()
        self.refinamientos.append(OUTPUT_FORMAT_TAGS[self.formato_seleccionado])
        self._update_current_format_indicator()
        self.actualizar_input()
        self.render_chips()

    def sync_output_format_with_content(self, output_text: str) -> str:
        if self.refinement_mode == REFINEMENT_MODE_RESPONSE:
            self.update_ui_format(OUTPUT_FORMAT_PARAGRAPH)
            self.requested_output_format = OUTPUT_FORMAT_PARAGRAPH
            return OUTPUT_FORMAT_PARAGRAPH
        self.update_ui_format(self.formato_seleccionado)
        return self.formato_seleccionado

    def add_refinement_lines(self, raw_value: str) -> bool:
        changed = False
        format_map = {value.lower(): key for key, value in OUTPUT_FORMAT_TAGS.items()}
        for line in (raw_value or "").splitlines():
            normalized = line.strip()
            if not normalized or normalized in self.refinamientos:
                continue
            format_selected = format_map.get(normalized.lower())
            if format_selected and self._supports_output_format():
                self.set_output_format(format_selected)
                changed = True
                continue
            self.refinamientos.append(normalized)
            changed = True
        if changed:
            self.actualizar_input()
            self.render_chips()
        return changed

    def actualizar_input(self) -> None:
        if not self.refine_text.winfo_exists():
            return
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
        if not self.refine_text.winfo_exists():
            return
        self.add_refinement_lines(self.refine_text.get("1.0", "end"))

    def toggle_refinement_dictation(self) -> None:
        if self.dictation_service is None:
            self._set_dictation_status("Dictado no disponible")
            return

        try:
            if not self.dictation_service.recording:
                if self.refine_text.winfo_exists():
                    self._dictation_snapshot = self.refine_text.get("1.0", "end").strip()
                    self.refine_text.focus_set()
                self.dictation_service.toggle_recording()
                safe_configure(self.dictation_button, text="⏹ Detener dictado")
                return
            self.dictation_service.toggle_recording()
            safe_configure(self.dictation_button, text="🎤 Dictar")
        except VoiceDictationError as exc:
            logger.exception("Error en dictado de refinamiento")
            self._show_dictation_error(str(exc))
            safe_configure(self.dictation_button, text="🎤 Dictar")
            return

        dictated_text = obtener_texto_dictado(self.refine_text, self._dictation_snapshot)
        if dictated_text:
            self.add_refinement_lines(dictated_text)

    def clear_refinements(self) -> None:
        self.refinamientos.clear()
        self._dictation_snapshot = ""
        if self._supports_output_format():
            self.set_output_format(self.output_format)
            return
        self.actualizar_input()

    def destroy(self) -> None:
        if hasattr(self, "dictation_service") and self.dictation_service is not None:
            try:
                self.dictation_service.destroy()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo detener servicio de dictado en destroy", exc_info=True)
        super().destroy()

    def get_prompt_final(self) -> str:
        refinamientos_prompt = list(self.refinamientos)
        if self.output_format == OUTPUT_FORMAT_PEDIDO:
            refinamientos_prompt = [OUTPUT_FORMAT_TAGS[OUTPUT_FORMAT_PEDIDO]]
        return build_refinement_prompt(
            base_text=self.texto_base,
            refinements=refinamientos_prompt,
            refinement_mode=self.refinement_mode,
            original_context=self.original_context,
            output_format=self.formato_seleccionado,
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
        self.sync_output_format_with_content(resultado)
        self.refinements_used += 1
        self.historial.append(
            {
                "version": len(self.historial) + 1,
                "refinement_mode": self.refinement_mode,
                "refinamientos": list(self.refinamientos),
                "output_format": self.formato_seleccionado,
                "resultado": resultado,
            }
        )
        self.refresh_history()

    def seed_history(self, initial_result: str) -> None:
        self.sync_output_format_with_content(initial_result)
        self.historial = [
            {
                "version": 1,
                "refinement_mode": self.refinement_mode,
                "refinamientos": [],
                "output_format": self.formato_seleccionado,
                "resultado": initial_result,
            }
        ]
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
        restored_format = str(selected_item.get("output_format") or OUTPUT_FORMAT_DEFAULT).strip().lower()
        self.update_ui_format(restored_format)
        self.on_restore_version(resultado)
        self.refinamientos.clear()
        self.refinamientos.extend(str(value) for value in refinamientos)
        self._update_current_format_indicator()
        self.actualizar_input()
        self.render_chips()
