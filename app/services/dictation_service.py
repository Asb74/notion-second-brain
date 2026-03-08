"""Reusable push-to-talk dictation service backed by OpenAI STT."""

from __future__ import annotations

import logging
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from app.utils.openai_client import build_openai_client

logger = logging.getLogger(__name__)


class DictationError(RuntimeError):
    """Controlled error for dictation failures."""


@dataclass
class DictationConfig:
    """Runtime dictation settings prepared for future realtime/streaming upgrades."""

    sample_rate: int = 16_000
    channels: int = 1
    model: str = "whisper-1"


class DictationService:
    """Capture microphone audio and transcribe using OpenAI audio APIs."""

    def __init__(self, api_key: str | None = None, openai_client: object | None = None, config: DictationConfig | None = None):
        self._api_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
        self._client = openai_client
        self._config = config or DictationConfig()
        self._recording_stream = None
        self._audio_chunks: list[object] = []
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        if self._recording:
            return

        try:
            import sounddevice as sd
        except ModuleNotFoundError as exc:
            raise DictationError("No se encontró soporte de micrófono (sounddevice).") from exc

        logger.info("Dictado iniciado")
        self._audio_chunks = []

        def _audio_callback(indata, _frames, _time, status) -> None:
            if status:
                logger.warning("Estado del stream de dictado: %s", status)
            self._audio_chunks.append(indata.copy())

        try:
            self._recording_stream = sd.InputStream(
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                dtype="int16",
                callback=_audio_callback,
            )
            self._recording_stream.start()
            self._recording = True
        except Exception as exc:  # noqa: BLE001
            self._recording = False
            self._recording_stream = None
            raise DictationError("No se pudo acceder al micrófono.") from exc

    def stop_recording_and_transcribe(self) -> str:
        if not self._recording:
            return ""

        logger.info("Dictado detenido, transcribiendo")

        try:
            if self._recording_stream is not None:
                self._recording_stream.stop()
                self._recording_stream.close()
        except Exception as exc:  # noqa: BLE001
            raise DictationError("No se pudo detener la grabación.") from exc
        finally:
            self._recording_stream = None
            self._recording = False

        if not self._audio_chunks:
            return ""

        try:
            import numpy as np
        except ModuleNotFoundError as exc:
            raise DictationError("No se encontró soporte de audio (numpy).") from exc

        audio_data = np.concatenate(self._audio_chunks, axis=0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_path = Path(temp_file.name)

        try:
            with wave.open(str(temp_path), "wb") as wav_file:
                wav_file.setnchannels(self._config.channels)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self._config.sample_rate)
                wav_file.writeframes(audio_data.tobytes())

            return self._transcribe_wav(temp_path).strip()
        except DictationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DictationError("No se pudo transcribir el audio") from exc
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("No se pudo eliminar archivo temporal de dictado: %s", temp_path)

    def _client_or_raise(self):
        if self._client is not None:
            return self._client

        if self._api_key:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise DictationError("La librería openai no está instalada.") from exc
            self._client = OpenAI(api_key=self._api_key, timeout=20.0)
            return self._client

        try:
            self._client = build_openai_client()
        except Exception as exc:  # noqa: BLE001
            raise DictationError("No se encontró API key de OpenAI para dictado.") from exc
        return self._client

    def _transcribe_wav(self, wav_path: Path) -> str:
        client = self._client_or_raise()
        try:
            with wav_path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=self._config.model,
                    file=audio_file,
                )
        except Exception as exc:  # noqa: BLE001
            raise DictationError("No se pudo transcribir el audio") from exc

        text = getattr(response, "text", "") if response is not None else ""
        if not text and isinstance(response, dict):
            text = str(response.get("text") or "")
        return text.strip()
