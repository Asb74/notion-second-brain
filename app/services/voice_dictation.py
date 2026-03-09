"""Servicio reutilizable de dictado por voz para widgets de Tkinter."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import tempfile
import threading
import tkinter as tk
import wave
from collections.abc import Callable
from pathlib import Path
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
        np, sd = self._validate_runtime_requirements()
        _ = np

        with self._lock:
            if self.recording:
                return

            self._target_widget = self.root.focus_get()
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

        self._set_status("🎤 Escuchando...")
        self._set_button_recording_state(True)

    def stop_recording(self) -> None:
        """Compatibilidad retroactiva."""
        self.stop_recording_and_transcribe()

    def stop_recording_and_transcribe(self) -> None:
        """Detiene la grabación y lanza la transcripción en un hilo."""
        np = importlib.import_module("numpy")

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

            chunks = list(self._audio_chunks)
            self._audio_chunks = []

        self._set_button_recording_state(False)

        if not chunks:
            self._set_status("No se capturó audio. Intenta hablar más cerca del micrófono.")
            if self._error_callback:
                self._error_callback("No se capturó audio. Intenta nuevamente.")
            return

        self._set_status("⏳ Transcribiendo...")

        try:
            audio_data = np.concatenate(chunks, axis=0)
            if audio_data.size == 0:
                raise RuntimeError("El audio capturado está vacío.")

            if getattr(audio_data, "ndim", 1) > 1:
                audio_data = audio_data.mean(axis=1)

            audio_data = np.clip(audio_data, -1.0, 1.0)
            pcm16_data = (audio_data * 32767.0).astype(np.int16)

            if pcm16_data.size == 0:
                raise RuntimeError("El audio convertido a PCM16 está vacío.")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                self._audio_path = temp_file.name

            with wave.open(self._audio_path, "wb") as wav_file:
                wav_file.setnchannels(self.CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.SAMPLE_RATE)
                wav_file.writeframes(pcm16_data.tobytes())

            audio_size = Path(self._audio_path).stat().st_size
            if audio_size == 0:
                raise RuntimeError("El archivo WAV temporal se creó vacío.")
            if audio_size > self.MAX_AUDIO_FILE_SIZE:
                raise RuntimeError("El audio supera 5 MB. Reduce la duración del dictado.")
        except Exception as exc:  # noqa: BLE001
            self._cleanup_temp_audio()
            logger.exception("Error preparando audio para transcripción")
            raise VoiceDictationError(str(exc)) from exc

        threading.Thread(target=self._transcribe_audio, daemon=True).start()

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

    def _transcribe_audio(self) -> None:
        """Envía audio WAV a OpenAI y actualiza la UI en el hilo principal."""
        audio_path = self._audio_path
        if not audio_path:
            self.root.after(0, lambda: self._on_transcription_error("No se encontró archivo temporal de audio."))
            return

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

            text = (getattr(transcription, "text", "") or "").strip()
            self.root.after(0, lambda: self._on_transcription_success(text))
        except api_timeout as exc:  # type: ignore[misc]
            logger.exception("Timeout al transcribir audio")
            self.root.after(0, lambda: self._on_transcription_error("Tiempo de espera agotado al transcribir audio."))
        except api_connection as exc:  # type: ignore[misc]
            logger.exception("Error de conexión al transcribir audio")
            self.root.after(0, lambda: self._on_transcription_error("Error de conexión con OpenAI. Revisa internet."))
        except api_status as exc:  # type: ignore[misc]
            status_code = getattr(exc, "status_code", "desconocido")
            logger.exception("Error HTTP de OpenAI al transcribir audio")
            self.root.after(0, lambda: self._on_transcription_error(f"OpenAI devolvió error HTTP {status_code}."))
        except VoiceDictationError as exc:
            logger.exception("Error controlado de dictado")
            self.root.after(0, lambda: self._on_transcription_error(str(exc)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado al transcribir audio")
            self.root.after(0, lambda: self._on_transcription_error(f"Error inesperado al transcribir: {exc}"))
        finally:
            self._cleanup_temp_audio()

    def _cleanup_temp_audio(self) -> None:
        audio_path = self._audio_path
        self._audio_path = None
        if not audio_path:
            return
        try:
            Path(audio_path).unlink(missing_ok=True)
        except OSError:
            logger.debug("No se pudo eliminar temporal de dictado: %s", audio_path)

    def _on_transcription_success(self, text: str) -> None:
        if text:
            self._insert_text(text)
            self._set_status("Listo")
            return

        self._on_transcription_error("La transcripción llegó vacía.")

    def _on_transcription_error(self, message: str) -> None:
        self._set_status(message)
        if self._error_callback:
            self._error_callback(message)

    def _insert_text(self, text: str) -> None:
        widget = self._target_widget
        if widget is None or not self._is_text_widget(widget):
            return

        try:
            if isinstance(widget, tk.Entry):
                widget.insert(tk.INSERT, text)
            elif isinstance(widget, (tk.Text, ScrolledText)):
                widget.insert(tk.INSERT, text)
            else:
                widget.insert("insert", text)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo insertar texto transcrito")

    @staticmethod
    def _is_text_widget(widget: tk.Widget | object) -> bool:
        return isinstance(widget, (tk.Entry, tk.Text, ScrolledText)) or hasattr(widget, "insert")

    def _set_status(self, text: str) -> None:
        if self._status_callback:
            self._status_callback(text)

    def _set_button_recording_state(self, active: bool) -> None:
        if self._button_state_callback:
            self._button_state_callback(active)
