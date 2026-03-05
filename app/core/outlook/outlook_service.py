"""Outlook integration helpers."""

from __future__ import annotations

import os

from app.config.mail_config import USER_EMAIL


class OutlookService:
    """Create drafts in Outlook desktop without auto-sending."""

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
    ) -> tuple[str, list[str]]:
        main = (main_recipient or "").strip().lower()
        mine = (my_email or "").strip().lower()
        configured_user = USER_EMAIL.strip().lower()

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

    @staticmethod
    def _get_mail_by_id(email_id: str):
        import win32com.client  # type: ignore[import-not-found]

        if not email_id or not email_id.strip():
            raise ValueError("email_id es obligatorio para responder el correo original")

        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        return namespace.GetItemFromID(email_id.strip())


    def reply_all(self, email_id: str) -> None:
        original = self._get_mail_by_id(email_id)
        reply = original.ReplyAll()
        reply.Display()

    def reply_all_with_body(self, email_id: str, body: str) -> None:
        mail = self._get_mail_by_id(email_id)
        reply = mail.ReplyAll()
        reply.Body = f"{body}\n\n{reply.Body}"
        reply.Display()

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
