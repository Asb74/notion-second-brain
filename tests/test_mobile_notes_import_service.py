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
