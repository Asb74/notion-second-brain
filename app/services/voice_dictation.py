"""Servicio reutilizable de dictado por voz para widgets de Tkinter."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import re
import tempfile
import threading
import time
import tkinter as tk
import wave
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

logger = logging.getLogger(__name__)


DEPENDENCY_LABELS = {
    "numpy": "numpy",
    "sounddevice": "sounddevice",
    "openai": "openai",
}


class VoiceDictationError(RuntimeError):
    """Error controlado de dictado."""


class VoiceDictationService:
    """Gestiona grabación, transcripción e inserción de texto para Tkinter."""

    SAMPLE_RATE = 16_000
    CHANNELS = 1
    MODEL = "gpt-4o-mini-transcribe"
    MAX_AUDIO_FILE_SIZE = 5 * 1024 * 1024
    PHRASE_WINDOW_SECONDS = 3
    _VOICE_COMMAND_DELETE_LAST = "borrar último fragmento"
    _VOICE_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
        ("punto y coma", ";"),
        ("dos puntos", ":"),
        ("nueva línea", "\n"),
        ("salto de línea", "\n"),
        ("abrir paréntesis", "("),
        ("cerrar paréntesis", ")"),
        ("punto", "."),
        ("coma", ","),
    )
    _PUNCTUATION_NO_LEADING_SPACE = tuple(".,:;)!?")

    def __init__(
        self,
        root: tk.Misc,
        *,
        status_callback: Callable[[str], None] | None = None,
        button_state_callback: Callable[[bool], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
        openai_client: Any | None = None,
    ) -> None:
        self.root = root
        self.recording = False
        self._stream: Any | None = None
        self._audio_chunks: list[Any] = []
        self._target_widget: tk.Widget | None = None
        self._status_callback = status_callback
        self._button_state_callback = button_state_callback
        self._error_callback = error_callback
        self._client = openai_client
        self._audio_path: str | None = None
        self._lock = threading.Lock()
        self._recognition_thread: threading.Thread | None = None
        self._dictation_history: dict[int, list[str]] = {}
        self.active = True
        self._after_ids: set[str] = set()

        if hasattr(self.root, "bind"):
            try:
                self.root.bind("<Destroy>", self._handle_root_destroy, add="+")
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo enlazar evento Destroy para servicio de dictado")

    def toggle_recording(self) -> None:
        """Alterna estado del botón micrófono (iniciar/detener)."""
        if not self.recording:
            self.start_recording()
            return
        self.stop_recording_and_transcribe()

    def _missing_dependencies(self) -> list[str]:
        return [name for name in DEPENDENCY_LABELS if importlib.util.find_spec(name) is None]

    def _validate_runtime_requirements(self) -> tuple[Any, Any]:
        missing = self._missing_dependencies()
        if missing:
            formatted = ", ".join(DEPENDENCY_LABELS[name] for name in missing)
            raise VoiceDictationError(f"Faltan dependencias para dictado: {formatted}.")

        np = importlib.import_module("numpy")
        sd = importlib.import_module("sounddevice")

        try:
            sd.check_input_settings(samplerate=self.SAMPLE_RATE, channels=self.CHANNELS)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Validación de micrófono falló")
            raise VoiceDictationError(
                "No se detectó un micrófono válido para grabar a 16kHz/mono."
            ) from exc

        return np, sd

    def start_recording(self) -> None:
        """Inicia captura de audio continua desde micrófono."""
        if not self.active:
            raise VoiceDictationError("El servicio de dictado ya no está activo.")

        np, sd = self._validate_runtime_requirements()
        _ = np

        from app.ui.dictation_widgets import _last_focused_widget

        target_widget = _last_focused_widget
        if (
            target_widget is None
            or not self._is_text_widget(target_widget)
            or not self._widget_exists(target_widget)
        ):
            message = "No se detectó un campo de texto activo para insertar el dictado."
            self._set_status(message)
            if self._error_callback:
                self._safe_callback(self._error_callback, message)
            raise VoiceDictationError(message)

        with self._lock:
            if self.recording:
                return

            self._target_widget = target_widget
            self._audio_chunks = []

            def _audio_callback(indata, _frames, _time, status) -> None:
                if status:
                    logger.warning("Estado del stream de dictado: %s", status)
                with self._lock:
                    self._audio_chunks.append(indata.copy())

            try:
                self._stream = sd.InputStream(
                    samplerate=self.SAMPLE_RATE,
                    channels=self.CHANNELS,
                    dtype="float32",
                    callback=_audio_callback,
                )
                self._stream.start()
                self.recording = True
            except Exception as exc:  # noqa: BLE001
                self.recording = False
                self._stream = None
                logger.exception("No se pudo iniciar grabación")
                raise VoiceDictationError("No se pudo acceder al micrófono para iniciar dictado.") from exc

            self._recognition_thread = threading.Thread(target=self._recognition_loop, daemon=True)
            self._recognition_thread.start()

        logger.info("Dictado iniciado")
        logger.info("Widget destino de dictado: %s", type(target_widget).__name__)
        self._set_status("🎙 Grabando...")
        self._set_button_recording_state(True)

    def stop_recording(self) -> None:
        """Compatibilidad retroactiva."""
        self.stop_recording_and_transcribe()

    def stop_recording_and_transcribe(self) -> None:
        """Detiene la grabación continua."""

        with self._lock:
            if not self.recording:
                return

            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
            except Exception as exc:  # noqa: BLE001
                logger.exception("No se pudo detener grabación")
                raise VoiceDictationError("No se pudo detener la grabación del micrófono.") from exc
            finally:
                self._stream = None
                self.recording = False

        self._set_button_recording_state(False)
        self._set_status("Listo")
        logger.info("Dictado detenido")

    def _recognition_loop(self) -> None:
        """Transcribe segmentos consecutivos sin bloquear la UI."""
        while True:
            time.sleep(self.PHRASE_WINDOW_SECONDS)

            with self._lock:
                recording = self.recording
                chunks = list(self._audio_chunks)
                self._audio_chunks = []

            if not chunks:
                if recording:
                    self._schedule_on_ui(lambda: self._set_status("🎙 Escuchando..."))
                    continue
                break

            try:
                self._schedule_on_ui(lambda: self._set_status("⏳ Transcribiendo..."))
                text = self._transcribe_chunks(chunks)
                if text:
                    logger.info("Fragmento transcrito: %s", text)
                    self._schedule_on_ui(lambda value=text: self._apply_transcribed_fragment(value))
                if recording:
                    self._schedule_on_ui(lambda: self._set_status("🎙 Escuchando..."))
            except Exception:  # noqa: BLE001
                logger.exception("Error de reconocimiento; se continuará escuchando")
                if recording:
                    self._schedule_on_ui(lambda: self._set_status("🎙 Escuchando..."))

            if not recording:
                break

        self._schedule_on_ui(lambda: self._set_status("Listo"))

    def _transcribe_chunks(self, chunks: list[Any]) -> str:
        np = importlib.import_module("numpy")
        audio_data = np.concatenate(chunks, axis=0)
        if audio_data.size == 0:
            return ""

        if getattr(audio_data, "ndim", 1) > 1:
            audio_data = audio_data.mean(axis=1)

        audio_data = np.clip(audio_data, -1.0, 1.0)
        pcm16_data = (audio_data * 32767.0).astype(np.int16)
        if pcm16_data.size == 0:
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            self._audio_path = temp_file.name

        try:
            with wave.open(self._audio_path, "wb") as wav_file:
                wav_file.setnchannels(self.CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.SAMPLE_RATE)
                wav_file.writeframes(pcm16_data.tobytes())

            audio_size = Path(self._audio_path).stat().st_size
            if audio_size == 0:
                return ""
            if audio_size > self.MAX_AUDIO_FILE_SIZE:
                raise RuntimeError("El audio supera 5 MB. Reduce la duración del dictado.")

            return self._transcribe_audio(self._audio_path)
        finally:
            self._cleanup_temp_audio()

    def _build_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            openai_module = importlib.import_module("openai")
            client_class = getattr(openai_module, "OpenAI")
            self._client = client_class(api_key=self._load_api_key())
            return self._client
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo inicializar cliente OpenAI")
            raise VoiceDictationError(str(exc)) from exc

    @staticmethod
    def _load_api_key() -> str:
        key_path = Path.home() / "AppData" / "Roaming" / "NotionSecondBrain" / "KeySecret.txt"
        if not key_path.exists():
            raise VoiceDictationError(f"No se encontró KeySecret.txt en: {key_path}")

        key = key_path.read_text(encoding="utf-8").strip()
        if not key:
            raise VoiceDictationError(f"El archivo KeySecret.txt está vacío: {key_path}")
        return key

    def _transcribe_audio(self, audio_path: str) -> str:
        """Envía audio WAV a OpenAI y retorna texto transcrito."""
        try:
            client = self._build_client()
            openai_module = importlib.import_module("openai")
            api_timeout = getattr(openai_module, "APITimeoutError", tuple())
            api_connection = getattr(openai_module, "APIConnectionError", tuple())
            api_status = getattr(openai_module, "APIStatusError", tuple())

            with open(audio_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model=self.MODEL,
                    file=audio_file,
                )
            return (getattr(transcription, "text", "") or "").strip()
        except api_timeout as exc:  # type: ignore[misc]
            logger.exception("Timeout al transcribir audio")
            raise VoiceDictationError("Tiempo de espera agotado al transcribir audio.") from exc
        except api_connection as exc:  # type: ignore[misc]
            logger.exception("Error de conexión al transcribir audio")
            raise VoiceDictationError("Error de conexión con OpenAI. Revisa internet.") from exc
        except api_status as exc:  # type: ignore[misc]
            status_code = getattr(exc, "status_code", "desconocido")
            logger.exception("Error HTTP de OpenAI al transcribir audio")
            raise VoiceDictationError(f"OpenAI devolvió error HTTP {status_code}.") from exc
        except VoiceDictationError:
            logger.exception("Error controlado de dictado")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado al transcribir audio")
            raise VoiceDictationError(f"Error inesperado al transcribir: {exc}") from exc

    def _cleanup_temp_audio(self) -> None:
        audio_path = self._audio_path
        self._audio_path = None
        if not audio_path:
            return
        try:
            Path(audio_path).unlink(missing_ok=True)
        except OSError:
            logger.debug("No se pudo eliminar temporal de dictado: %s", audio_path)

    def _apply_transcribed_fragment(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return

        if clean_text.casefold() == self._VOICE_COMMAND_DELETE_LAST:
            self._delete_last_fragment()
            return

        processed_text = self._apply_voice_commands(clean_text)
        self._insert_text(processed_text)

    def _apply_voice_commands(self, text: str) -> str:
        """Aplica comandos de voz básicos antes de insertar el texto."""
        updated = f" {text} "
        commands_applied = False

        for command, replacement in self._VOICE_COMMAND_PATTERNS:
            pattern = re.compile(rf"(?<!\\w){re.escape(command)}(?!\\w)", flags=re.IGNORECASE)
            updated, count = pattern.subn(replacement, updated)
            if count:
                commands_applied = True

        updated = re.sub(r"\s+([\.,:;\)])", r"\1", updated)
        updated = re.sub(r"\(\s+", "(", updated)
        updated = re.sub(r" *\n *", "\n", updated)
        updated = re.sub(r" {2,}", " ", updated)
        updated = updated.strip()

        if commands_applied:
            logger.info("Comandos de voz aplicados")
        return updated

    def _insert_text(self, text: str) -> None:
        widget = self._target_widget
        if widget is None or not self._is_text_widget(widget) or not self._widget_exists(widget):
            return

        if not text:
            return

        try:
            if isinstance(widget, (tk.Entry, ttk.Entry)):
                current_text = widget.get()
                fragment = text.replace("\n", " ")
                value = self._merge_text(str(current_text), fragment)
                widget.insert(tk.END, value)
            elif isinstance(widget, (tk.Text, ScrolledText)):
                current_text = widget.get("1.0", "end-1c")
                value = self._merge_text(str(current_text), text)
                widget.insert("end", value)
                self._append_history(widget, value)
            else:
                value = text
                if hasattr(widget, "get"):
                    try:
                        current_text = widget.get()
                        value = self._merge_text(str(current_text), text)
                    except Exception:  # noqa: BLE001
                        logger.debug("No se pudo consultar contenido previo en widget personalizado")
                widget.insert("end", value)
            logger.info("Fragmento insertado correctamente")
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo insertar texto transcrito")

    def _delete_last_fragment(self) -> None:
        widget = self._target_widget
        if not isinstance(widget, (tk.Text, ScrolledText)) or not self._widget_exists(widget):
            logger.debug("Comando borrar último fragmento omitido: widget no soportado")
            return

        history = self._dictation_history.get(id(widget), [])
        if not history:
            return

        last_fragment = history.pop()
        if not last_fragment:
            return

        try:
            end_index = widget.index("end-1c")
            start_index = widget.index(f"{end_index}-{len(last_fragment)}c")
            existing = widget.get(start_index, end_index)
            if existing == last_fragment:
                widget.delete(start_index, end_index)
                logger.info("Borrado último fragmento")
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo borrar el último fragmento dictado")

    def _append_history(self, widget: tk.Widget, inserted_text: str) -> None:
        key = id(widget)
        if key not in self._dictation_history:
            self._dictation_history[key] = []
        self._dictation_history[key].append(inserted_text)

    def _merge_text(self, current_text: str, new_fragment: str) -> str:
        if not current_text:
            return new_fragment

        if not new_fragment:
            return ""

        if current_text.endswith(("\n", " ", "\t")):
            return new_fragment

        if new_fragment.startswith(("\n", *self._PUNCTUATION_NO_LEADING_SPACE)):
            return new_fragment

        return f" {new_fragment}"

    @staticmethod
    def _is_text_widget(widget: tk.Widget | object) -> bool:
        return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ScrolledText)) or hasattr(widget, "insert")

    @staticmethod
    def _widget_exists(widget: tk.Widget | object | None) -> bool:
        if widget is None or not hasattr(widget, "winfo_exists"):
            return False
        try:
            return bool(widget.winfo_exists())
        except Exception:  # noqa: BLE001
            return False

    def _schedule_on_ui(self, callback: Callable[[], None]) -> str | None:
        if not self.active or not self._widget_exists(self.root):
            return None

        callback_id: str | None = None

        def _runner() -> None:
            if callback_id:
                self._after_ids.discard(callback_id)
            if not self.active:
                return
            try:
                callback()
            except Exception:  # noqa: BLE001
                logger.exception("Error ejecutando callback en UI de dictado")

        try:
            callback_id = self.root.after(0, _runner)
        except Exception:  # noqa: BLE001
            return None

        self._after_ids.add(callback_id)
        return callback_id

    def _cancel_pending_callbacks(self) -> None:
        if not self._widget_exists(self.root):
            self._after_ids.clear()
            return

        for callback_id in tuple(self._after_ids):
            try:
                self.root.after_cancel(callback_id)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._after_ids.discard(callback_id)

    def _safe_callback(self, callback: Callable[..., None] | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001
            logger.exception("Callback de dictado falló y fue ignorado")

    def _handle_root_destroy(self, _event: tk.Event | None = None) -> None:
        self.destroy()

    def destroy(self) -> None:
        self.active = False
        try:
            self.stop_recording()
        except Exception:  # noqa: BLE001
            pass
        self._cancel_pending_callbacks()
        self._target_widget = None

    def _set_status(self, text: str) -> None:
        self._safe_callback(self._status_callback, text)

    def _set_button_recording_state(self, active: bool) -> None:
        self._safe_callback(self._button_state_callback, active)
