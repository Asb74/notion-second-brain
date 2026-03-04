"""Outlook integration helpers."""

from __future__ import annotations


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

        normalized_to = [to_list] if isinstance(to_list, str) else (to_list or [])
        normalized_cc = [cc_list] if isinstance(cc_list, str) else (cc_list or [])
        all_candidates = cls._parse_addresses(",".join([*normalized_to, *normalized_cc]))

        normalized_seen: set[str] = set()
        clean_cc: list[str] = []
        for candidate in all_candidates:
            normalized = candidate.lower()
            if not normalized or normalized in normalized_seen:
                continue
            if normalized == main or normalized == mine:
                continue
            normalized_seen.add(normalized)
            clean_cc.append(candidate)

        clean_main = "" if main == mine else (main_recipient or "").strip()
        return clean_main, clean_cc

    def create_draft(
        self,
        subject: str,
        body: str,
        original_from: str,
        original_to: str,
        original_cc: str,
        my_email: str,
        original_reply_to: str = "",
    ) -> None:
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
        draft.Display()
