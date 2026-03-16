import base64
from typing import List
import logging

from app.config.config_paths import GMAIL_CREDENTIALS, GMAIL_TOKEN
from app.core.google.google_auth_manager import GoogleAuthManager

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
logger = logging.getLogger(__name__)


class GmailClient:

    def __init__(
        self,
        credentials_path: str = GMAIL_CREDENTIALS,
        token_path: str = GMAIL_TOKEN,
    ):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()

    def _authenticate(self):
        auth = GoogleAuthManager(
            credentials_path=self.credentials_path,
            token_path=self.token_path,
            scopes=SCOPES,
        )
        creds = auth.get_credentials()
        return build("gmail", "v1", credentials=creds)

    def _is_invalid_grant_error(self, exc: Exception) -> bool:
        if isinstance(exc, HttpError) and "invalid_grant" in str(exc):
            return True
        return False

    def _reauthenticate_oauth(self):
        logger.info("Token revocado, iniciando reautenticación OAuth")
        self.service = self._authenticate()

    def _execute_with_reauth(self, operation):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            if not self._is_invalid_grant_error(exc):
                raise

            logger.info("Token Gmail expirado o revocado. Reautenticando...")
            logger.info("Token revocado, iniciando reautenticación OAuth")
            self._reauthenticate_oauth()
            return operation()

    def list_messages(self, max_results: int = 10) -> List[str]:
        results = self._execute_with_reauth(
            lambda: self.service.users().messages().list(
                userId="me",
                maxResults=max_results
            ).execute()
        )

        messages = results.get("messages", [])
        return [msg["id"] for msg in messages]

    def list_unread_messages(self, max_results: int = 20) -> List[str]:
        results = self._execute_with_reauth(
            lambda: self.service.users().messages().list(
                userId="me",
                labelIds=["UNREAD"],
                maxResults=max_results
            ).execute()
        )

        messages = results.get("messages", [])
        return [msg["id"] for msg in messages]

    def mark_as_read(self, message_id: str):
        self._execute_with_reauth(
            lambda: self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        )

    def list_messages_by_label(
        self,
        label_name: str,
        max_results: int = 10
    ) -> List[str]:
        labels_response = self._execute_with_reauth(
            lambda: self.service.users().labels().list(
                userId="me"
            ).execute()
        )
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

        results = self._execute_with_reauth(
            lambda: self.service.users().messages().list(
                userId="me",
                labelIds=[label_id],
                maxResults=max_results
            ).execute()
        )

        messages = results.get("messages", [])
        return [msg["id"] for msg in messages]

    def get_message(self, message_id: str, format: str = "full"):
        return self._execute_with_reauth(
            lambda: self.service.users().messages().get(
                userId="me",
                id=message_id,
                format=format
            ).execute()
        )

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        response = self._execute_with_reauth(
            lambda: self.service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
        )
        data = response.get("data", "")
        if not data:
            return b""
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    def get_message_subject(self, message_id: str) -> str:
        message = self._execute_with_reauth(
            lambda: self.service.users().messages().get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["Subject"]
            ).execute()
        )

        headers = message["payload"]["headers"]
        for header in headers:
            if header["name"] == "Subject":
                return header["value"]

        return ""

    def get_my_email(self) -> str:
        profile = self._execute_with_reauth(
            lambda: self.service.users().getProfile(userId="me").execute()
        )
        return profile.get("emailAddress", "")
