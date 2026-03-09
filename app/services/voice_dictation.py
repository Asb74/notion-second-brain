"""Servicio centralizado para dictado por voz con OpenAI Speech-to-Text."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)


class VoiceDictationError(RuntimeError):
    """Error controlado de dictado."""


class VoiceDictationService:
    """Gestiona grabación continua y transcripción para toda la aplicación."""

    SAMPLE_RATE = 16_000
    CHANNELS = 1
    MODEL = "gpt-4o-mini-transcribe"

    def __init__(self) -> None:
        self.recording = False
        self._recording_stream = None
        self._audio_chunks: list[object] = []
        self._client: OpenAI | None = None

    def start_recording(self) -> None:
        """Inicia stream de audio continuo."""
        if self.recording:
            return

        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            raise VoiceDictationError("No se encontró soporte de micrófono (sounddevice).") from exc

        self._audio_chunks = []

        def _callback(indata, _frames, _time, status) -> None:
            if status:
                logger.warning("Estado del stream de dictado: %s", status)
            self._audio_chunks.append(indata.copy())

        try:
            self._recording_stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="float32",
                callback=_callback,
            )
            self._recording_stream.start()
            self.recording = True
            logger.info("Dictado iniciado")
        except Exception as exc:  # noqa: BLE001
            self._recording_stream = None
            self.recording = False
            raise VoiceDictationError("No se pudo acceder al micrófono.") from exc

    def stop_recording(self) -> str:
        """Detiene el stream, transcribe y devuelve texto."""
        if not self.recording:
            return ""

        try:
            if self._recording_stream is not None:
                self._recording_stream.stop()
                self._recording_stream.close()
        except Exception as exc:  # noqa: BLE001
            raise VoiceDictationError("No se pudo detener la grabación.") from exc
        finally:
            self._recording_stream = None
            self.recording = False

        if not self._audio_chunks:
            return ""

        try:
            import numpy as np
            import soundfile as sf
        except Exception as exc:  # noqa: BLE001
            raise VoiceDictationError("No se encontró soporte de audio (numpy/soundfile).") from exc

        audio_data = np.concatenate(self._audio_chunks, axis=0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            wav_path = Path(temp_file.name)

        try:
            sf.write(str(wav_path), audio_data, self.SAMPLE_RATE, subtype="PCM_16")
            return self.transcribe_audio(wav_path)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("No se pudo eliminar archivo temporal de dictado: %s", wav_path)

    def transcribe_audio(self, wav_path: Path) -> str:
        """Envía archivo WAV a OpenAI y devuelve el texto."""
        client = self._client_or_raise()
        try:
            with wav_path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=self.MODEL,
                    file=audio_file,
                )
        except Exception as exc:  # noqa: BLE001
            raise VoiceDictationError("No se pudo transcribir el audio") from exc

        text = getattr(response, "text", "") if response is not None else ""
        if not text and isinstance(response, dict):
            text = str(response.get("text") or "")
        return text.strip()

    def _client_or_raise(self) -> OpenAI:
        if self._client is not None:
            return self._client

        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise VoiceDictationError("No se encontró API key de OpenAI para dictado.")

        self._client = OpenAI(api_key=api_key)
        return self._client


voice_dictation_service = VoiceDictationService()
