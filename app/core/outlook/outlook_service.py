"""Outlook integration helpers."""

from __future__ import annotations

import logging
import os
import sqlite3

from app.core.config.user_context import get_user_email

logger = logging.getLogger(__name__)


class OutlookService:
    """Create drafts in Outlook desktop without auto-sending."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn
        self._user_email_cache: str | None = None

    def _require_user_email(self) -> str:
        if self._user_email_cache:
            return self._user_email_cache
        if self.conn is None:
            raise ValueError("No hay conexión de base de datos para resolver user_profile")

        user_email = get_user_email(self.conn)
        if not user_email:
            raise ValueError("No hay email configurado en user_profile")

        self._user_email_cache = user_email
        return user_email

    @staticmethod
    def _parse_addresses(raw_value: str | None) -> list[str]:
        if not raw_value:
            return []

        import re

        tokens = [token.strip() for token in re.split(r"[;,]", raw_value) if token.strip()]
        addresses: list[str] = []
        for token in tokens:
            match = re.search(r"<([^>]+)>", token)
            candidate = (match.group(1) if match else token).strip().strip('"')
            if candidate:
                addresses.append(candidate)
        return addresses

    @classmethod
    def clean_recipients(
        cls,
        to_list: list[str] | str,
        cc_list: list[str] | str,
        main_recipient: str,
        my_email: str,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[str, list[str]]:
        main = (main_recipient or "").strip().lower()
        mine = (my_email or "").strip().lower()
        configured_user = get_user_email(conn) if conn is not None else ""

        normalized_to = [to_list] if isinstance(to_list, str) else (to_list or [])
        normalized_cc = [cc_list] if isinstance(cc_list, str) else (cc_list or [])
        all_candidates = cls._parse_addresses(",".join([*normalized_to, *normalized_cc]))

        normalized_seen: set[str] = {main} if main else set()
        clean_cc: list[str] = []
        for candidate in all_candidates:
            normalized = candidate.lower()
            if not normalized or normalized in normalized_seen:
                continue
            if normalized in {main, mine, configured_user}:
                continue
            normalized_seen.add(normalized)
            clean_cc.append(candidate)

        clean_main = "" if main in {mine, configured_user} else (main_recipient or "").strip()
        return clean_main, clean_cc

    @classmethod
    def construir_destinatarios_respuesta(
        cls,
        email_original: dict[str, str] | None,
        usuario_actual: str,
    ) -> dict[str, list[str]]:
        """Construye destinatarios para respuesta tipo "Responder a todos"."""

        payload = email_original or {}
        usuario_normalizado = (usuario_actual or "").strip().lower()

        from_list = cls._parse_addresses(payload.get("from", ""))
        to_list = cls._parse_addresses(payload.get("to", ""))
        cc_list = cls._parse_addresses(payload.get("cc", ""))

        to_final: list[str] = []
        cc_final: list[str] = []
        seen_to: set[str] = set()
        seen_cc: set[str] = set()

        for candidate in [*from_list, *to_list]:
            normalized = candidate.strip().lower()
            if not normalized or normalized == usuario_normalizado or normalized in seen_to:
                continue
            seen_to.add(normalized)
            to_final.append(normalized)

        for candidate in cc_list:
            normalized = candidate.strip().lower()
            if not normalized or normalized == usuario_normalizado:
                continue
            if normalized in seen_cc:
                continue
            if normalized in seen_to:
                continue
            seen_cc.add(normalized)
            cc_final.append(normalized)

        return {"to": to_final, "cc": cc_final}

    def reply_all(self, email_id: str) -> None:
        import win32com.client  # type: ignore[import-not-found]

        entry_id = (email_id or "").strip()
        if not entry_id:
            raise ValueError("entry_id es obligatorio para responder el correo original")

        outlook = win32com.client.Dispatch("Outlook.Application")
        session = outlook.Session

        try:
            original = session.GetItemFromID(entry_id)
        except Exception:  # noqa: BLE001
            original = None

        if original is None:
            logger.warning("Email no encontrado en Outlook entry_id=%s", entry_id)
            return

        original.Display()
        reply = original.ReplyAll()
        reply.Display()

    def reply_all_with_body(self, email_id: str, body: str) -> bool:
        import win32com.client  # type: ignore[import-not-found]

        entry_id = (email_id or "").strip()
        if not entry_id:
            raise ValueError("entry_id es obligatorio")

        user_email = self._require_user_email()

        outlook = win32com.client.DispatchEx("Outlook.Application")

        try:
            explorer = outlook.ActiveExplorer()
            if explorer:
                explorer.Activate()
        except Exception:  # noqa: BLE001
            pass

        session = outlook.Session

        try:
            mail = session.GetItemFromID(entry_id)
        except Exception:  # noqa: BLE001
            logger.warning("Email no encontrado en Outlook entry_id=%s", entry_id)
            return False

        mail.Display()
        reply = mail.Reply()

        remitente = getattr(mail, "SenderEmailAddress", "")
        if not remitente:
            remitente = getattr(mail, "Sender", "")
        destinatarios = self.construir_destinatarios_respuesta(
            {
                "from": remitente,
                "to": getattr(mail, "To", ""),
                "cc": getattr(mail, "CC", ""),
            },
            usuario_actual=user_email,
        )
        reply.To = "; ".join(destinatarios["to"])
        reply.CC = "; ".join(destinatarios["cc"])

        reply.Body = f"{body}\n\n---\n{reply.Body}"
        reply.Display()
        logger.info("Reply draft created for entry_id=%s", entry_id)
        return True

    def create_draft(
        self,
        subject: str,
        body: str,
        original_from: str,
        original_to: str,
        original_cc: str,
        my_email: str,
        original_reply_to: str = "",
        attachment_paths: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        import win32com.client  # type: ignore[import-not-found]

        main_recipient_candidates = self._parse_addresses(original_reply_to) or self._parse_addresses(original_from)
        main_recipient = main_recipient_candidates[0] if main_recipient_candidates else ""
        clean_main, clean_cc = self.clean_recipients(
            to_list=original_to,
            cc_list=original_cc,
            main_recipient=main_recipient,
            my_email=my_email,
            conn=self.conn,
        )

        outlook = win32com.client.Dispatch("Outlook.Application")
        draft = outlook.CreateItem(0)
        draft.To = clean_main
        draft.CC = "; ".join(clean_cc)
        draft.Subject = subject
        draft.Body = body
        for attachment_path in attachment_paths or []:
            if attachment_path:
                draft.Attachments.Add(Source=self._validate_attachment_path(attachment_path))
        draft.Display()
        return clean_main, clean_cc

    def create_forward_draft(
        self,
        subject: str,
        body: str,
        attachment_paths: list[str] | None = None,
    ) -> None:
        import win32com.client  # type: ignore[import-not-found]

        outlook = win32com.client.Dispatch("Outlook.Application")
        draft = outlook.CreateItem(0)
        draft.Subject = subject
        draft.Body = body
        for attachment_path in attachment_paths or []:
            if attachment_path:
                draft.Attachments.Add(Source=self._validate_attachment_path(attachment_path))
        draft.Display()

    @staticmethod
    def _validate_attachment_path(path: str) -> str:
        absolute = os.path.abspath(path)
        if not os.path.exists(absolute):
            raise FileNotFoundError(f"Adjunto no encontrado (ruta no existe): {absolute}")
        return absolute
