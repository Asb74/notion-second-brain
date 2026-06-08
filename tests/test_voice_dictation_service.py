from __future__ import annotations

import sys
import types
from pathlib import Path

from app.services.voice_dictation import VoiceDictationService


class _Root:
    def winfo_exists(self) -> bool:
        return True


class _FakeTextWidget:
    def __init__(self, content: str, cursor: int) -> None:
        self.content = content
        self.cursor = cursor
        self.insert_calls: list[tuple[str, str]] = []
        self.see_calls: list[str] = []

    def winfo_exists(self) -> bool:
        return True

    def get(self, start: str, end: str | None = None) -> str:
        if (start, end) == ("1.0", "insert"):
            return self.content[: self.cursor]
        return self.content

    def index(self, index: str) -> int:
        assert index == "insert"
        return self.cursor

    def insert(self, index: str, value: str) -> None:
        self.insert_calls.append((index, value))
        assert index == "insert"
        self.content = self.content[: self.cursor] + value + self.content[self.cursor :]
        self.cursor += len(value)

    def see(self, index: str) -> None:
        self.see_calls.append(index)


class _FakeTranscriptions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(text=" hola ")


class _FakeAudio:
    def __init__(self) -> None:
        self.transcriptions = _FakeTranscriptions()


class _FakeClient:
    def __init__(self) -> None:
        self.audio = _FakeAudio()


def test_insert_text_uses_cursor_prefix_and_keeps_cursor_after_insert() -> None:
    service = VoiceDictationService(_Root())
    widget = _FakeTextWidget("Hola hemos revisado", cursor=len("Hola hemos"))
    service._target_widget = widget

    service._insert_text("ya")

    assert widget.insert_calls == [("insert", " ya")]
    assert widget.content == "Hola hemos ya revisado"
    assert widget.cursor == len("Hola hemos ya")
    assert widget.see_calls == ["insert"]


def test_transcribe_audio_forces_default_spanish_language(tmp_path: Path, monkeypatch) -> None:
    fake_openai = types.ModuleType("openai")
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake audio")
    client = _FakeClient()
    service = VoiceDictationService(_Root(), openai_client=client)

    text = service._transcribe_audio(str(audio_path))

    assert text == "hola"
    assert client.audio.transcriptions.kwargs["model"] == service.MODEL
    assert client.audio.transcriptions.kwargs["language"] == service.DEFAULT_LANGUAGE == "es"
