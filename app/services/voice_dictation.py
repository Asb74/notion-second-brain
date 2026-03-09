"""Servicio reutilizable de dictado por voz para widgets de Tkinter."""

from __future__ import annotations

import logging
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter.scrolledtext import ScrolledText

import numpy as np
import sounddevice as sd
import soundfile as sf
from openai import OpenAI

logger = logging.getLogger(__name__)


class VoiceDictationError(RuntimeError):
    """Error controlado de dictado."""


class VoiceDictationService:
    """Gestiona grabación, transcripción e inserción de texto para Tkinter."""

    SAMPLE_RATE = 16_000
    CHANNELS = 1
    MODEL = "gpt-4o-mini-transcribe"

    def __init__(
        self,
        root: tk.Misc,
        *,
        status_callback: Callable[[str], None] | None = None,
        button_state_callback: Callable[[bool], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
        openai_client: OpenAI | None = None,
    ) -> None:
        self.root = root
        self.recording = False
        self._stream: sd.InputStream | None = None
        self._audio_chunks: list[np.ndarray] = []
        self._recording_widget: tk.Widget | None = None
        self._status_callback = status_callback
        self._button_state_callback = button_state_callback
        self._error_callback = error_callback
        self._client = openai_client or OpenAI()
        self._audio_path: str | None = None
        self._lock = threading.Lock()

    def toggle_recording(self) -> None:
        """Alterna estado del botón micrófono (iniciar/detener)."""
        if not self.recording:
            self.start_recording()
            return
        self.stop_recording()

    def start_recording(self) -> None:
        """Inicia captura de audio continua desde micrófono."""
        with self._lock:
            if self.recording:
                return

            self._recording_widget = self.root.focus_get()
            self._audio_chunks = []

            def _audio_callback(indata, _frames, _time, status) -> None:
                if status:
                    logger.warning("Estado del stream de dictado: %s", status)
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
                raise VoiceDictationError("No se pudo acceder al micrófono") from exc

        self._set_status("🎤 Escuchando...")
        self._set_button_recording_state(True)

    def stop_recording(self) -> None:
        """Detiene la grabación y lanza la transcripción en un hilo."""
        with self._lock:
            if not self.recording:
                return

            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
            except Exception as exc:  # noqa: BLE001
                logger.exception("No se pudo detener grabación")
                raise VoiceDictationError("No se pudo detener la grabación") from exc
            finally:
                self._stream = None
                self.recording = False

            if not self._audio_chunks:
                self._set_status("Listo")
                self._set_button_recording_state(False)
                return

            audio_data = np.concatenate(self._audio_chunks, axis=0)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                self._audio_path = temp_file.name

            sf.write(self._audio_path, audio_data, self.SAMPLE_RATE)

        self._set_status("⏳ Transcribiendo...")
        self._set_button_recording_state(False)
        threading.Thread(target=self._transcribe_audio, daemon=True).start()

    def _transcribe_audio(self) -> None:
        """Envía audio WAV a OpenAI y actualiza la UI en el hilo principal."""
        audio_path = self._audio_path
        if not audio_path:
            self.root.after(0, lambda: self._set_status("Listo"))
            return

        try:
            with open(audio_path, "rb") as audio_file:
                transcription = self._client.audio.transcriptions.create(
                    model=self.MODEL,
                    file=audio_file,
                )
            text = (getattr(transcription, "text", "") or "").strip()
            self.root.after(0, lambda: self._on_transcription_success(text))
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo transcribir el audio")
            self.root.after(0, self._on_transcription_error)
        finally:
            try:
                import os

                os.unlink(audio_path)
            except OSError:
                logger.debug("No se pudo eliminar temporal de dictado: %s", audio_path)
            self._audio_path = None

    def _on_transcription_success(self, text: str) -> None:
        if text:
            self._insert_text(text)
        self._set_status("Listo")

    def _on_transcription_error(self) -> None:
        self._set_status("No se pudo transcribir el audio")
        if self._error_callback:
            self._error_callback("No se pudo transcribir el audio")

    def _insert_text(self, text: str) -> None:
        widget = self._recording_widget
        if widget is None or not self._is_text_widget(widget):
            return

        try:
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
