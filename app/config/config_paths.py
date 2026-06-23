"""Shared filesystem paths for application configuration and secrets."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DATA_FOLDER = "SansebasNexus"
LEGACY_APP_DATA_FOLDER = "NotionSecondBrain"
GMAIL_CREDENTIALS_FILENAME = "gmail_credentials.json"
GMAIL_TOKEN_FILENAME = "gmail_token.json"
CALENDAR_CREDENTIALS_FILENAME = "calendar_credentials.json"
CALENDAR_TOKEN_FILENAME = "calendar_token.json"


def _roaming_appdata_root() -> Path:
    """Return the user's roaming AppData directory, with a cross-platform fallback."""
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


def app_data_dir() -> Path:
    """Return the centralized local data directory used by Sansebas Nexus."""
    return _roaming_appdata_root() / APP_DATA_FOLDER


def legacy_app_data_dir() -> Path:
    """Return the previous app data directory kept for backwards compatibility."""
    return _roaming_appdata_root() / LEGACY_APP_DATA_FOLDER


def google_credentials_config_dir() -> Path:
    """Return the preferred folder for Google credential JSON files."""
    return app_data_dir() / "secrets"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def is_running_from_source() -> bool:
    """Return True when the app is not running from a PyInstaller executable."""
    return not bool(getattr(sys, "frozen", False))


def google_credentials_candidates(filename: str = GMAIL_CREDENTIALS_FILENAME) -> list[Path]:
    """Return credential locations in the supported lookup order."""
    candidates = [
        app_data_dir() / "secrets" / filename,
        legacy_app_data_dir() / "secrets" / filename,
    ]
    if is_running_from_source():
        candidates.append(_project_root() / "secrets" / filename)
    return candidates


def find_google_credentials(filename: str = GMAIL_CREDENTIALS_FILENAME) -> Path | None:
    """Locate a Google credentials JSON file, logging each attempted path."""
    for candidate in google_credentials_candidates(filename):
        logger.info("GOOGLE_CREDENTIALS: buscando en %s", candidate)
        if candidate.exists():
            logger.info("GOOGLE_CREDENTIALS: encontrado %s", candidate)
            return candidate
    logger.warning("GOOGLE_CREDENTIALS: no encontrado")
    return None


def google_credentials_not_found_message(filename: str = GMAIL_CREDENTIALS_FILENAME) -> str:
    """Build a clear user-facing error that lists every attempted path."""
    tried_paths = "\n".join(f"- {path}" for path in google_credentials_candidates(filename))
    return (
        "No se encontró el archivo de credenciales Google.\n\n"
        "Rutas probadas:\n"
        f"{tried_paths}\n\n"
        "Puedes configurarlo desde Configuración → Integraciones → "
        "Seleccionar credenciales Google."
    )


def copy_google_credentials(source_path: str | Path) -> Path:
    """Copy a selected Google credentials JSON to the preferred config folder."""
    source = Path(source_path)
    if source.suffix.lower() != ".json":
        raise ValueError("Selecciona un archivo .json de credenciales Google.")
    if not source.exists():
        raise FileNotFoundError(f"No existe el archivo seleccionado: {source}")

    destination = google_credentials_config_dir() / GMAIL_CREDENTIALS_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    logger.info("GOOGLE_CREDENTIALS: copiado a %s", destination)
    return destination


def open_google_credentials_config_dir() -> None:
    """Open the preferred Google credentials config folder in the OS file manager."""
    folder = google_credentials_config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "startfile"):
        os.startfile(folder)  # type: ignore[attr-defined]
        return
    import subprocess

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(folder)])


GMAIL_CREDENTIALS = str(google_credentials_config_dir() / GMAIL_CREDENTIALS_FILENAME)
GMAIL_TOKEN = str(google_credentials_config_dir() / GMAIL_TOKEN_FILENAME)

CALENDAR_CREDENTIALS = str(google_credentials_config_dir() / CALENDAR_CREDENTIALS_FILENAME)
CALENDAR_TOKEN = str(google_credentials_config_dir() / CALENDAR_TOKEN_FILENAME)


# Backwards-compatible alias for older imports.
SECRETS_PATH = str(google_credentials_config_dir())


def knowledge_attachments_dir() -> Path:
    """Return the internal storage directory for Knowledge Manager attachments."""
    return app_data_dir() / "knowledge" / "attachments"
