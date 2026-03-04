"""Outlook integration helpers."""

from __future__ import annotations


class OutlookService:
    """Create drafts in Outlook desktop without auto-sending."""

    def create_draft(self, to: str, subject: str, body: str) -> None:
        import win32com.client  # type: ignore[import-not-found]

        outlook = win32com.client.Dispatch("Outlook.Application")
        draft = outlook.CreateItem(0)
        draft.To = to
        draft.Subject = subject
        draft.Body = body
        draft.Display()

