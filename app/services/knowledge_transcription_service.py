"""Provider-independent audio transcription for Knowledge attachments."""

from __future__ import annotations

import json
import logging
import mimetypes
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from app.core.openai_client import build_openai_client

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".mpeg", ".mpga", ".webm"}
MAX_PROVIDER_BYTES = 24 * 1024 * 1024
CHUNK_SECONDS = 20 * 60
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"


def is_audio_file(path: str | Path, mime_type: str = "") -> bool:
    candidate = Path(path)
    mime = (mime_type or mimetypes.guess_type(str(candidate))[0] or "").lower()
    return (
        candidate.suffix.lower() in AUDIO_EXTENSIONS
        or mime.startswith("audio/")
        or mime == "video/mp4"
    )


def find_media_binary(name: str) -> str | None:
    """Find ffmpeg/ffprobe beside a frozen app first, then on the system PATH."""
    executable = f"{name}.exe" if sys.platform.startswith("win") else name
    roots = [Path(sys.executable).resolve().parent]
    if getattr(sys, "_MEIPASS", None):
        roots.insert(0, Path(sys._MEIPASS))  # type: ignore[attr-defined]
    roots.extend([Path.cwd(), Path(__file__).resolve().parents[2] / "bin"])
    for root in roots:
        for candidate in (root / executable, root / "ffmpeg" / executable):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def get_audio_duration(path: str | Path) -> float | None:
    ffprobe = find_media_binary("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return float(json.loads(completed.stdout)["format"]["duration"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("KNOWLEDGE_TRANSCRIPTION: duration unavailable reason=%s", exc)
        return None


def _openai_transcribe(path: Path, language: str, client: Any | None = None) -> str:
    client = client or build_openai_client()
    with path.open("rb") as audio:
        response = client.audio.transcriptions.create(
            model=OPENAI_TRANSCRIPTION_MODEL,
            file=audio,
            language=language or None,
        )
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        return str(response.get("text") or "").strip()
    return str(getattr(response, "text", "") or "").strip()


def _split_audio(path: Path, output_dir: Path) -> list[Path]:
    ffmpeg = find_media_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "El audio excede el límite del proveedor y ffmpeg no está disponible para dividirlo."
        )
    pattern = output_dir / "chunk_%05d.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-f",
            "segment",
            "-segment_time",
            str(CHUNK_SECONDS),
            "-acodec",
            "libmp3lame",
            "-b:a",
            "64k",
            str(pattern),
        ],
        check=True,
        capture_output=True,
        timeout=60 * 30,
    )
    chunks = sorted(output_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg no produjo fragmentos de audio.")
    return chunks


def transcribe_audio(
    path: str | Path,
    language: str = "es",
    engine: str | None = None,
    *,
    client: Any | None = None,
    transcriber: Callable[[Path, str], str] | None = None,
) -> dict[str, object]:
    """Transcribe an audio, chunking safely when provider limits may be exceeded."""
    audio_path = Path(path)
    selected_engine = (engine or "openai").lower()
    result: dict[str, object] = {
        "text": "",
        "status": "error",
        "engine": selected_engine,
        "language": language,
        "duration": None,
        "error": None,
    }
    try:
        if not audio_path.is_file():
            raise FileNotFoundError(f"No existe el audio: {audio_path}")
        if not is_audio_file(audio_path):
            raise ValueError(
                f"Formato de audio no soportado: {audio_path.suffix or 'desconocido'}"
            )
        if selected_engine != "openai":
            result.update(
                status="unavailable",
                error=f"Motor de transcripción no disponible: {selected_engine}",
            )
            return result
        duration = get_audio_duration(audio_path)
        result["duration"] = duration
        logger.info("KNOWLEDGE_TRANSCRIPTION: duration=%s", duration)
        call = transcriber or (
            lambda chunk, lang: _openai_transcribe(chunk, lang, client)
        )
        requires_chunks = audio_path.stat().st_size > MAX_PROVIDER_BYTES or bool(
            duration and duration > CHUNK_SECONDS
        )
        if requires_chunks:
            with tempfile.TemporaryDirectory(prefix="nexus_transcription_") as temp:
                chunks = _split_audio(audio_path, Path(temp))
                logger.info(
                    "KNOWLEDGE_TRANSCRIPTION: chunking=True chunks=%s", len(chunks)
                )
                texts = [call(chunk, language).strip() for chunk in chunks]
        else:
            texts = [call(audio_path, language).strip()]
        text = "\n\n".join(part for part in texts if part).strip()
        result.update(text=text, status="ok" if text else "empty", error=None)
        logger.info("KNOWLEDGE_TRANSCRIPTION: completed chars=%s", len(text))
    except Exception as exc:  # noqa: BLE001
        result.update(status="error", error=str(exc))
        logger.exception("KNOWLEDGE_TRANSCRIPTION: error reason=%s", exc)
    return result
