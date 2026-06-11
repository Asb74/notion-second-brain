from app.ui.knowledge_metadata_dialog import KnowledgeMetadataDialog


def test_email_attachment_preselection_marks_documents():
    attachment = {
        "filename": "DECLARACION DE TRAZABILIDAD 2026.doc",
        "mimeType": "application/msword",
        "size": 50 * 1024,
    }

    assert KnowledgeMetadataDialog._should_preselect_attachment(attachment) is True


def test_email_attachment_preselection_unmarks_small_signature_images():
    attachment = {
        "filename": "logo_empresa.png",
        "mimeType": "image/png",
        "size": 12 * 1024,
    }

    assert KnowledgeMetadataDialog._should_preselect_attachment(attachment) is False


def test_email_attachment_preselection_keeps_large_non_corporate_images():
    attachment = {
        "filename": "captura_error.jpg",
        "mimeType": "image/jpeg",
        "size": 250 * 1024,
    }

    assert KnowledgeMetadataDialog._should_preselect_attachment(attachment) is True
