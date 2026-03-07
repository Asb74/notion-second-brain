from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarClient:

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None

        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(
                self.token_path, SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, "w") as token_file:
                token_file.write(creds.to_json())

        return build("calendar", "v3", credentials=creds)

    def list_events(self, days: int = 30) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        max_time = now + timedelta(days=days)

        results = self.service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=max_time.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        return results.get("items", [])

    def create_event(
        self,
        title: str,
        description: str,
        start_datetime: datetime,
        end_datetime: datetime
    ) -> str:
        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": self._format_datetime(start_datetime)},
            "end": {"dateTime": self._format_datetime(end_datetime)},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 30},
                ],
            },
        }

        created_event = self.service.events().insert(
            calendarId="primary",
            body=event,
        ).execute()

        return created_event["id"]

    def update_event(self, event_id: str, data: dict[str, Any]) -> str:
        updated_event = self.service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body=data,
        ).execute()

        return updated_event["id"]

    def delete_event(self, event_id: str) -> str:
        self.service.events().delete(
            calendarId="primary",
            eventId=event_id,
        ).execute()

        return event_id

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
