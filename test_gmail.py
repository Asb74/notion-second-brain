from app.core.email.gmail_client import GmailClient

client = GmailClient(
    credentials_path="secrets/gmail_credentials.json",
    token_path="secrets/gmail_token.json"
)

messages = client.list_unread_messages(5)

print("Mensajes no leídos encontrados:")
for msg_id in messages:
    subject = client.get_message_subject(msg_id)
    print(f"ID: {msg_id} | Asunto: {subject}")
    client.mark_as_read(msg_id)
