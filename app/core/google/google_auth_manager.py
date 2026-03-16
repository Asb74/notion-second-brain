"""Reusable Google OAuth credential manager."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


logger = logging.getLogger(__name__)


class GoogleAuthManager:
    """Centralized OAuth token handling for Google services."""

    def __init__(
        self,
        credentials_path: str,
        token_path: str,
        scopes: list[str],
        auth_event_callback: Callable[[str], None] | None = None,
    ):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.scopes = scopes
        self.auth_event_callback = auth_event_callback
        self.reauthentication_required = False

    def get_credentials(self, force_reauthentication: bool = False) -> Credentials:
        self.reauthentication_required = False

        if force_reauthentication:
            logger.info("Reautenticación Google forzada")
            self._delete_token_file()
            creds = self._run_oauth_flow(trigger_reauth=True)
            self._save_token(creds)
            return creds

        creds = self._load_token()

        if not creds:
            logger.info("No existe token Google; iniciando flujo OAuth")
            creds = self._run_oauth_flow()
            self._save_token(creds)
            return creds

        if creds.valid:
            logger.info("Google token cargado desde disco")
            return creds

        logger.info("Google token expirado o inválido")
        creds = self._refresh_or_reauth(creds)
        self._save_token(creds)
        return creds

    def _load_token(self) -> Credentials | None:
        token_file = Path(self.token_path)
        if not token_file.exists():
            return None

        try:
            creds = Credentials.from_authorized_user_file(str(token_file), self.scopes)
            logger.info("Google token cargado desde disco")
            return creds
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Token Google inválido en disco; eliminando y relanzando OAuth: %s", exc)
            self._delete_token_file()
            return None

    def _refresh_or_reauth(self, creds: Credentials) -> Credentials:
        if creds.expired and creds.refresh_token:
            logger.info("Google token expirado; intentando refresh")
            try:
                creds.refresh(Request())
                logger.info("Google token refrescado correctamente")
                return creds
            except RefreshError as exc:
                logger.warning(
                    "RefreshError detectado; eliminando token y relanzando OAuth: %s",
                    exc,
                )
                self._delete_token_file()
                return self._run_oauth_flow(trigger_reauth=True)

        logger.info("No se puede refrescar token Google; relanzando OAuth")
        return self._run_oauth_flow(trigger_reauth=True)

    def _run_oauth_flow(self, trigger_reauth: bool = False) -> Credentials:
        credentials_file = Path(self.credentials_path)
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"No se encontró el archivo de credenciales Google en: {self.credentials_path}"
            )

        if trigger_reauth:
            self.reauthentication_required = True
            self._emit_auth_event("reauthentication_required")
            self._emit_auth_event("reauthentication_started")

        try:
            flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, self.scopes)
            creds = flow.run_local_server(port=0)
            logger.info("OAuth completado correctamente")
            if trigger_reauth:
                self._emit_auth_event("reauthentication_succeeded")
            return creds
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error durante el flujo OAuth interactivo de Google")
            raise RuntimeError("Falló el flujo OAuth interactivo de Google") from exc

    def _emit_auth_event(self, event_name: str) -> None:
        if self.auth_event_callback is None:
            return
        try:
            self.auth_event_callback(event_name)
        except Exception:  # noqa: BLE001
            logger.exception("Error enviando evento de autenticación Google: %s", event_name)

    def _save_token(self, creds: Credentials) -> None:
        token_file = Path(self.token_path)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Token guardado correctamente")

    def _delete_token_file(self) -> None:
        token_file = Path(self.token_path)
        if token_file.exists():
            token_file.unlink()
