import os
import base64
from typing import List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailClient:

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None

        # Si ya existe token guardado
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(
                self.token_path, SCOPES
            )

        # Si no hay credenciales válidas
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(self.token_path, "w") as token:
                token.write(creds.to_json())

        return build("gmail", "v1", credentials=creds)

    def list_messages(self, max_results: int = 10) -> List[str]:
        results = self.service.users().messages().list(
            userId="me",
            maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        return [msg["id"] for msg in messages]

    def list_messages_by_label(
        self,
        label_name: str,
        max_results: int = 10
    ) -> List[str]:
        labels_response = self.service.users().labels().list(
            userId="me"
        ).execute()
        labels = labels_response.get("labels", [])

        label_id = next(
            (
                label["id"]
                for label in labels
                if label.get("name") == label_name
            ),
            None
        )

        if not label_id:
            print(f"Etiqueta '{label_name}' no encontrada.")
            return []

        results = self.service.users().messages().list(
            userId="me",
            labelIds=[label_id],
            maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        return [msg["id"] for msg in messages]

    def get_message_subject(self, message_id: str) -> str:
        message = self.service.users().messages().get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject"]
        ).execute()

        headers = message["payload"]["headers"]
        for header in headers:
            if header["name"] == "Subject":
                return header["value"]

        return ""
