"""Google Calendar API client for agenda features."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarClient:
    """Simple wrapper around Google Calendar API operations."""

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()

    def _authenticate(self):
        creds: Credentials | None = None
        token_file = Path(self.token_path)

        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)

            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json(), encoding="utf-8")

        return build("calendar", "v3", credentials=creds)

    def list_events(self, days: int = 30) -> list[dict]:
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        response = (
            self.service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return response.get("items", [])

    def create_event(self, title, description, start_datetime, end_datetime):
        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": self._to_iso(start_datetime)},
            "end": {"dateTime": self._to_iso(end_datetime)},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 30},
                ],
            },
        }
        return self.service.events().insert(calendarId="primary", body=event).execute()

    def update_event(self, event_id, data):
        return self.service.events().patch(calendarId="primary", eventId=event_id, body=data).execute()

    def delete_event(self, event_id):
        self.service.events().delete(calendarId="primary", eventId=event_id).execute()

    @staticmethod
    def _to_iso(value: str | datetime) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

