import sqlite3

from app.core.email.gmail_client import GmailClient
from app.core.email.mail_ingestion_service import MailIngestionService


def main() -> None:
    client = GmailClient(
        credentials_path="secrets/gmail_credentials.json",
        token_path="secrets/gmail_token.json",
    )

    connection = sqlite3.connect("app.db")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                gmail_id TEXT PRIMARY KEY,
                thread_id TEXT,
                subject TEXT,
                sender TEXT,
                received_at TEXT,
                body_text TEXT,
                body_html TEXT,
                has_attachments INTEGER,
                raw_payload_json TEXT,
                processed_at TEXT,
                status TEXT
            );
            """
        )

        ingestion_service = MailIngestionService(client, connection)
        processed_ids = ingestion_service.sync_unread_emails(max_results=20)

        print("IDs procesados:")
        print(processed_ids)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
