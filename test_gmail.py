from app.core.email.gmail_client import GmailClient

client = GmailClient(
    credentials_path="secrets/gmail_credentials.json",
    token_path="secrets/gmail_token.json"
)

messages = client.list_messages_by_label("Para_Procesar", 5)

print("IDs encontrados:")
print(messages)

for msg_id in messages:
    subject = client.get_message_subject(msg_id)
    print("Asunto:", subject)
