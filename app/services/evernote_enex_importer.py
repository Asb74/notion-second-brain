"""Pilot Evernote ENEX parser for Knowledge imports."""

from __future__ import annotations

import base64
import html
import logging
import mimetypes
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

_EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/html": ".html",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(parent: ET.Element | None, name: str, default: str = "") -> str:
    if parent is None:
        return default
    for child in list(parent):
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return default


def _children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).name).strip(" ._")
    return cleaned or "adjunto"


def _extension_for_mime(mime_type: str) -> str:
    return _EXTENSION_BY_MIME.get(mime_type.strip().lower()) or mimetypes.guess_extension(mime_type) or ".bin"


def _html_to_text(content_html: str) -> str:
    """Return readable plain text from Evernote ENML without extra dependencies."""
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", content_html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(div|p|li|h[1-6]|tr|table)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*en-media\b[^>]*>", "\n[adjunto]\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _resource_filename(resource: ET.Element, mime_type: str, index: int) -> str:
    attributes = next((child for child in list(resource) if _local_name(child.tag) == "resource-attributes"), None)
    filename = _child_text(attributes, "file-name")
    if filename:
        return _safe_filename(filename)
    extension = _extension_for_mime(mime_type)
    return f"evernote_adjunto_{index}{extension}"


def _parse_resource(resource: ET.Element, index: int) -> dict[str, object]:
    mime_type = _child_text(resource, "mime")
    data_node = next((child for child in list(resource) if _local_name(child.tag) == "data"), None)
    raw_data = b""
    if data_node is not None and data_node.text:
        try:
            raw_data = base64.b64decode(re.sub(r"\s+", "", data_node.text), validate=False)
        except Exception as exc:  # noqa: BLE001
            logger.info("EVERNOTE_IMPORT: recurso omitido reason=base64_invalido index=%s error=%s", index, exc)
            raw_data = b""
    filename = _resource_filename(resource, mime_type, index)
    return {
        "filename": filename,
        "mime": mime_type,
        "data": raw_data,
        "size": len(raw_data),
    }


def _parse_note(note: ET.Element) -> dict[str, object]:
    title = _child_text(note, "title") or "Nota sin título"
    created = _child_text(note, "created")
    updated = _child_text(note, "updated")
    content_html = _child_text(note, "content")
    tags = [_child_text(tag, "", "") or (tag.text or "").strip() for tag in _children(note, "tag")]
    tags = [tag for tag in tags if tag]
    notebook = _child_text(note, "notebook") or None
    resources = [_parse_resource(resource, index + 1) for index, resource in enumerate(_children(note, "resource"))]
    return {
        "title": title,
        "created": created,
        "updated": updated,
        "content_html": content_html,
        "content_text": _html_to_text(content_html),
        "tags": tags,
        "notebook": notebook,
        "resources": resources,
    }


def parse_enex_file(path: str | Path) -> list[dict[str, object]]:
    """Parse an Evernote .enex file into normalized note dictionaries."""
    enex_path = Path(path)
    notes: list[dict[str, object]] = []
    logger.info("EVERNOTE_IMPORT: archivo seleccionado path=%s", enex_path)
    for _event, elem in ET.iterparse(enex_path, events=("end",)):
        if _local_name(elem.tag) != "note":
            continue
        try:
            notes.append(_parse_note(elem))
        except Exception as exc:  # noqa: BLE001
            logger.info("EVERNOTE_IMPORT: error title=%s reason=%s", _child_text(elem, "title") or "", exc)
        finally:
            elem.clear()
    logger.info("EVERNOTE_IMPORT: notas detectadas=%s", len(notes))
    return notes
