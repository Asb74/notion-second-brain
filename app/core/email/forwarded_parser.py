"""Utilities to extract recipients from forwarded email bodies."""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REAL_SENDER_RE = re.compile(r"De:\s.*?<(.+?)>", re.IGNORECASE)


@dataclass
class _ForwardedFields:
    from_value: str = ""
    to_value: str = ""
    cc_value: str = ""
    reply_to_value: str = ""


def _extract_emails(text: str) -> list[str]:
    if not text:
        return []
    return EMAIL_RE.findall(text)


def extract_forwarded_headers(body_text: str) -> dict:
    """
    Devuelve dict con keys: from, to_list, cc_list.

    Busca bloque desde '-----Mensaje original-----' hasta una línea vacía doble o fin.
    Dentro, busca líneas que empiezan por:
      'De:' / 'From:'
      'Para:' / 'To:'
      'CC:' / 'Cc:'
      'Reply-To:'
    Soporta valores partidos en varias líneas (continuaciones) hasta que aparece otro campo o línea vacía.
    """
    if not body_text:
        return {}

    text = body_text.replace("\r\n", "\n").replace("\r", "\n")

    marker = "-----Mensaje original-----"
    idx = text.lower().find(marker.lower())
    if idx == -1:
        return {}

    block = text[idx + len(marker):]
    block = block[:4000]

    lines = block.split("\n")
    current_key: str | None = None
    buf = _ForwardedFields()

    def is_field_line(line: str) -> str | None:
        low = line.strip().lower()
        if low.startswith("de:") or low.startswith("from:"):
            return "from"
        if low.startswith("para:") or low.startswith("to:"):
            return "to"
        if low.startswith("cc:"):
            return "cc"
        if low.startswith("reply-to:"):
            return "reply_to"
        return None

    empty_streak = 0
    for line in lines:
        if not line.strip():
            empty_streak += 1
            if any((buf.from_value, buf.to_value, buf.cc_value, buf.reply_to_value)) and empty_streak >= 2:
                break
            continue

        empty_streak = 0
        key = is_field_line(line)
        if key:
            current_key = key
            value = line.split(":", 1)[1].strip()
            if key == "from":
                buf.from_value = (f"{buf.from_value} {value}").strip()
            elif key == "to":
                buf.to_value = (f"{buf.to_value} {value}").strip()
            elif key == "cc":
                buf.cc_value = (f"{buf.cc_value} {value}").strip()
            elif key == "reply_to":
                buf.reply_to_value = (f"{buf.reply_to_value} {value}").strip()
            continue

        if current_key == "from":
            buf.from_value = (f"{buf.from_value} {line.strip()}").strip()
        elif current_key == "to":
            buf.to_value = (f"{buf.to_value} {line.strip()}").strip()
        elif current_key == "cc":
            buf.cc_value = (f"{buf.cc_value} {line.strip()}").strip()
        elif current_key == "reply_to":
            buf.reply_to_value = (f"{buf.reply_to_value} {line.strip()}").strip()

    from_emails = _extract_emails(buf.from_value)
    to_emails = _extract_emails(buf.to_value)
    cc_emails = _extract_emails(buf.cc_value)
    reply_to_emails = _extract_emails(buf.reply_to_value)

    parsed = {
        "from": from_emails[0] if from_emails else "",
        "to_list": to_emails,
        "cc_list": cc_emails,
        "reply_to": reply_to_emails[0] if reply_to_emails else "",
    }
    if not parsed["from"] and not parsed["to_list"] and not parsed["cc_list"] and not parsed["reply_to"]:
        return {}
    return parsed


def extract_real_sender(email_body: str, original_sender: str = "") -> str:
    if not email_body:
        return original_sender

    match = REAL_SENDER_RE.search(email_body)
    if match:
        return match.group(1).strip()
    return original_sender


def extract_original_recipients(body_text: str) -> dict:
    """Backward-compatible wrapper returning comma-separated from/to/cc."""
    parsed = extract_forwarded_headers(body_text)
    if not parsed:
        return {}
    return {
        "from": parsed.get("from", ""),
        "to": ", ".join(parsed.get("to_list", [])),
        "cc": ", ".join(parsed.get("cc_list", [])),
    }
