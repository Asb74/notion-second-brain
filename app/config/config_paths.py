from pathlib import Path

SECRETS_PATH = r"C:\notion-second-brain\secrets"

GMAIL_CREDENTIALS = SECRETS_PATH + r"\gmail_credentials.json"
GMAIL_TOKEN = SECRETS_PATH + r"\gmail_token.json"

CALENDAR_CREDENTIALS = SECRETS_PATH + r"\calendar_credentials.json"
CALENDAR_TOKEN = SECRETS_PATH + r"\calendar_token.json"


def app_data_dir() -> Path:
    """Return the centralized local data directory used by the application."""
    return Path.home() / "AppData" / "Roaming" / "NotionSecondBrain"


def knowledge_attachments_dir() -> Path:
    """Return the internal storage directory for Knowledge Manager attachments."""
    return app_data_dir() / "knowledge" / "attachments"
