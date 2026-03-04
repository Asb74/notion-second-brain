import sqlite3

from app.persistence.email_repository import EmailRepository


def test_bulk_update_type_and_save_label() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = EmailRepository(conn)

    conn.execute(
        """
        INSERT INTO emails (gmail_id, subject, sender, received_at, body_text, status, category, type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("g1", "Hola", "a@sansebas.es", "2024-01-01T00:00:00+00:00", "body", "new", "pending", "other"),
    )
    conn.commit()

    repo.bulk_update_type(["g1"], "priority")
    repo.save_labels_for_emails(["g1"], "priority", source="user")

    row = conn.execute("SELECT type FROM emails WHERE gmail_id = 'g1'").fetchone()
    label_row = conn.execute("SELECT label, source FROM email_labels WHERE gmail_id = 'g1'").fetchone()

    assert row["type"] == "priority"
    assert label_row["label"] == "priority"
    assert label_row["source"] == "user"



def test_get_attachments_returns_saved_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    repo = EmailRepository(conn)

    repo.save_attachment(
        gmail_id="g1",
        filename="factura.pdf",
        mime_type="application/pdf",
        local_path="attachments/g1/factura.pdf",
        size=120,
    )

    rows = repo.get_attachments("g1")

    assert len(rows) == 1
    assert rows[0]["filename"] == "factura.pdf"
    assert rows[0]["local_path"] == "attachments/g1/factura.pdf"
