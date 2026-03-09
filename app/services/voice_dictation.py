"""Servicio centralizado para dictado por voz con OpenAI Speech-to-Text."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000
_CHANNELS = 1
_MODEL = "gpt-4o-mini-transcribe"

_recording_stream = None
_audio_chunks: list[object] = []
_is_recording = False


class VoiceDictationError(RuntimeError):
    """Error controlado de dictado."""


def _transcribir_archivo(wav_path: Path) -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise VoiceDictationError("No se encontró API key de OpenAI para dictado.")

    client = OpenAI(api_key=api_key)
    try:
        with wav_path.open("rb") as audio_file:
            respuesta = client.audio.transcriptions.create(
                model=_MODEL,
                file=audio_file,
            )
    except Exception as exc:  # noqa: BLE001
        raise VoiceDictationError("No se pudo transcribir el audio") from exc

    text = getattr(respuesta, "text", "") if respuesta is not None else ""
    if not text and isinstance(respuesta, dict):
        text = str(respuesta.get("text") or "")
    return text.strip()


def dictar_texto(accion: str = "toggle") -> str:
    """Controla el ciclo de dictado.

    - accion="iniciar": inicia grabación y devuelve "__RECORDING__".
    - accion="detener": detiene, transcribe y devuelve el texto.
    - accion="toggle": alterna entre iniciar y detener.
    """

    global _recording_stream, _audio_chunks, _is_recording

    normalized_action = accion.lower().strip()
    if normalized_action not in {"toggle", "iniciar", "detener"}:
        raise VoiceDictationError("Acción de dictado no soportada.")

    should_start = normalized_action == "iniciar" or (normalized_action == "toggle" and not _is_recording)
    if should_start:
        _audio_chunks = []

        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            raise VoiceDictationError("No se encontró soporte de micrófono (sounddevice).") from exc

        def _callback(indata, _frames, _time, status):
            if status:
                logger.warning("Estado del stream de dictado: %s", status)
            _audio_chunks.append(indata.copy())

        try:
            _recording_stream = sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype="float32",
                callback=_callback,
            )
            _recording_stream.start()
            _is_recording = True
        except Exception as exc:  # noqa: BLE001
            _recording_stream = None
            _is_recording = False
            raise VoiceDictationError("No se pudo acceder al micrófono.") from exc
        return "__RECORDING__"

    if not _is_recording:
        return ""

    try:
        if _recording_stream is not None:
            _recording_stream.stop()
            _recording_stream.close()
    except Exception as exc:  # noqa: BLE001
        raise VoiceDictationError("No se pudo detener la grabación.") from exc
    finally:
        _recording_stream = None
        _is_recording = False

    if not _audio_chunks:
        return ""

    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:  # noqa: BLE001
        raise VoiceDictationError("No se encontró soporte de audio (numpy/soundfile).") from exc

    audio_data = np.concatenate(_audio_chunks, axis=0)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
        wav_path = Path(temp_file.name)

    try:
        sf.write(str(wav_path), audio_data, _SAMPLE_RATE, subtype="PCM_16")
        return _transcribir_archivo(wav_path)
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("No se pudo eliminar archivo temporal de dictado: %s", wav_path)
