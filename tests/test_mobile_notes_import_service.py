import sqlite3
import sys
import types

from app.services.mobile_notes_import_service import (
    DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET,
    MobileNotesImportService,
)


class _FakeCredentialsModule:
    @staticmethod
    def Certificate(path):
        return {"path": path}


class _FakeFirestoreModule:
    @staticmethod
    def client(app=None):
        return {"app": app}


def test_initialize_firebase_uses_configured_firebasestorage_bucket(tmp_path, monkeypatch):
    credentials_path = tmp_path / "firebase.json"
    credentials_path.write_text('{"project_id": "sansebas-nexus"}', encoding="utf-8")
    calls = {}

    firebase_admin = types.ModuleType("firebase_admin")

    def get_app(name):
        calls["get_app_name"] = name
        raise ValueError

    def initialize_app(cred, options=None, name=None):
        calls["initialize_cred"] = cred
        calls["initialize_options"] = options
        calls["initialize_name"] = name
        return {"name": name, "options": options}

    firebase_admin.get_app = get_app
    firebase_admin.initialize_app = initialize_app

    credentials_module = types.ModuleType("firebase_admin.credentials")
    credentials_module.Certificate = _FakeCredentialsModule.Certificate
    firestore_module = types.ModuleType("firebase_admin.firestore")
    firestore_module.client = _FakeFirestoreModule.client
    storage_module = types.ModuleType("firebase_admin.storage")

    class FakeBucket:
        name = DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET

    def bucket(name=None, app=None):
        calls["bucket_name"] = name
        calls["bucket_app"] = app
        return FakeBucket()

    storage_module.bucket = bucket
    firebase_admin.credentials = credentials_module
    firebase_admin.firestore = firestore_module
    firebase_admin.storage = storage_module
    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "firebase_admin.firestore", firestore_module)
    monkeypatch.setitem(sys.modules, "firebase_admin.storage", storage_module)

    conn = sqlite3.connect(":memory:")
    try:
        service = MobileNotesImportService(conn, credentials_path=credentials_path)
        _db, bucket_client = service.initialize_firebase()
    finally:
        conn.close()

    assert bucket_client.name == DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET
    assert calls["initialize_options"] == {"storageBucket": DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET}
    assert calls["bucket_name"] == DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET


def test_normalize_storage_path_keeps_mobile_attachment_path():
    raw_path = "users/user-1/nexus_mobile_notes/note-1/photo.jpg"

    normalized = MobileNotesImportService._normalize_storage_path(raw_path, DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET)

    assert normalized == raw_path


def test_summary_message_includes_storage_delete_counters():
    from app.services.mobile_notes_import_service import MobileNotesImportSummary

    summary = MobileNotesImportSummary(
        notes_found=1,
        notes_imported=1,
        attachments_expected=2,
        attachments_found=2,
        attachments_downloaded=2,
        storage_attachments_deleted=2,
        storage_delete_errors=0,
        duration_seconds=1.25,
    )

    message = summary.to_message()

    assert "Adjuntos borrados de Storage: 2" in message
    assert "Errores de borrado Storage: 0" in message


def test_delete_storage_attachment_normalizes_path_and_deletes_blob(tmp_path):
    credentials_path = tmp_path / "firebase.json"
    credentials_path.write_text('{"project_id": "sansebas-nexus"}', encoding="utf-8")
    deleted = {}

    class FakeBlob:
        def __init__(self, path):
            self.path = path

        def exists(self):
            return True

        def delete(self):
            deleted["path"] = self.path

    class FakeBucket:
        name = DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET

        def blob(self, path):
            deleted["blob_path"] = path
            return FakeBlob(path)

    conn = sqlite3.connect(":memory:")
    try:
        service = MobileNotesImportService(conn, credentials_path=credentials_path)
        service._db = object()
        service._bucket = FakeBucket()
        service.delete_storage_attachment(
            f"gs://{DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET}/users/user-1/nexus_mobile_notes/note-1/photo.jpg"
        )
    finally:
        conn.close()

    assert deleted["blob_path"] == "users/user-1/nexus_mobile_notes/note-1/photo.jpg"
    assert deleted["path"] == "users/user-1/nexus_mobile_notes/note-1/photo.jpg"


def test_attachment_storage_delete_markers_write_expected_firestore_fields(tmp_path):
    writes = []

    class FakeDocument:
        def __init__(self, path):
            self.path = path

        def collection(self, name):
            return FakeCollection(f"{self.path}/{name}")

        def set(self, data, merge=False):
            writes.append((self.path, data, merge))

    class FakeCollection:
        def __init__(self, path):
            self.path = path

        def document(self, doc_id):
            return FakeDocument(f"{self.path}/{doc_id}")

    class FakeDb:
        def collection(self, name):
            return FakeCollection(name)

    conn = sqlite3.connect(":memory:")
    try:
        service = MobileNotesImportService(conn, credentials_path=tmp_path / "firebase.json")
        service._db = FakeDb()
        service._bucket = object()
        service.mark_attachment_storage_deleted("note-1", "att-1", "2026-07-02T00:00:00+00:00")
        service.mark_attachment_storage_delete_error("note-1", "att-2", "2026-07-02T00:00:00+00:00", "boom")
    finally:
        conn.close()

    assert writes[0][0] == "nexus_mobile_notes/note-1/attachments/att-1"
    assert writes[0][1]["sync_status"] == "deleted"
    assert writes[0][1]["storage_deleted"] is True
    assert writes[0][1]["error_message"] is None
    assert writes[0][2] is True
    assert writes[1][0] == "nexus_mobile_notes/note-1/attachments/att-2"
    assert writes[1][1]["sync_status"] == "imported"
    assert writes[1][1]["storage_deleted"] is False
    assert writes[1][1]["delete_error_message"] == "boom"
    assert writes[1][2] is True
