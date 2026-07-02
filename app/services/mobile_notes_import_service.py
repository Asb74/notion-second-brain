"""Import pending Sansebas Nexus Mobile notes from Firebase into local Knowledge."""

from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.config_paths import knowledge_attachments_dir
from app.persistence.knowledge_repository import KnowledgeRepository
from app.services.mobile_firebase_publish_service import DEFAULT_FIREBASE_CREDENTIALS_PATH

logger = logging.getLogger(__name__)

FIREBASE_APP_NAME = "sansebas_nexus_mobile_import"
NOTES_COLLECTION = "nexus_mobile_notes"
DESKTOP_ID = "nexus_desktop"


class MobileNotesImportError(RuntimeError):
    """Raised when mobile note import cannot be started or completed."""


@dataclass(frozen=True)
class DownloadedMobileAttachment:
    """Local representation of a Firebase Storage attachment downloaded for import."""

    mobile_attachment_id: str
    original_filename: str
    stored_path: Path
    storage_path: str
    mime_type: str = ""
    file_size: int = 0


@dataclass(frozen=True)
class MobileNotesImportSummary:
    """Counters for a Firebase → Desktop mobile notes import run."""

    notes_found: int = 0
    notes_imported: int = 0
    attachments_downloaded: int = 0
    errors: int = 0
    duration_seconds: float = 0.0

    def to_message(self) -> str:
        return (
            f"Notas encontradas: {self.notes_found}\n"
            f"Notas importadas: {self.notes_imported}\n"
            f"Adjuntos descargados: {self.attachments_downloaded}\n"
            f"Errores: {self.errors}\n"
            f"Duración: {self.duration_seconds:.2f} segundos"
        )


class MobileNotesImportService:
    """Import uploaded mobile notes from Firestore and Firebase Storage into Knowledge."""

    def __init__(self, conn: sqlite3.Connection, credentials_path: Path | str | None = None, desktop_id: str = DESKTOP_ID):
        self.conn = conn
        self.repo = KnowledgeRepository(conn)
        self.credentials_path = Path(credentials_path) if credentials_path else DEFAULT_FIREBASE_CREDENTIALS_PATH
        self.desktop_id = desktop_id.strip() or DESKTOP_ID
        self._db: Any | None = None
        self._bucket: Any | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def initialize_firebase(self) -> tuple[Any, Any]:
        """Initialize Firebase Admin SDK and return Firestore and Storage clients."""
        logger.info("MOBILE_NOTES_IMPORT: inicializando Firebase con ruta %s", self.credentials_path)
        self._validate_credentials_file()
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore, storage
        except ImportError as exc:
            raise MobileNotesImportError(
                "No está instalada la dependencia firebase-admin. Instala Firebase Admin SDK para importar notas móviles."
            ) from exc

        try:
            options: dict[str, str] = {}
            project_id = self._credentials_project_id()
            if project_id:
                options["storageBucket"] = f"{project_id}.appspot.com"
            try:
                app = firebase_admin.get_app(FIREBASE_APP_NAME)
            except ValueError:
                cred = credentials.Certificate(str(self.credentials_path))
                app = firebase_admin.initialize_app(cred, options=options, name=FIREBASE_APP_NAME)
            self._db = firestore.client(app=app)
            self._bucket = storage.bucket(app=app)
            logger.info("MOBILE_NOTES_IMPORT: Firebase inicializado correctamente")
            return self._db, self._bucket
        except Exception as exc:  # noqa: BLE001
            logger.exception("MOBILE_NOTES_IMPORT: error inicializando Firebase")
            raise MobileNotesImportError(f"No se pudo conectar con Firebase: {exc}") from exc

    def fetch_pending_notes(self) -> list[dict[str, Any]]:
        """Read Firestore mobile notes pending Desktop import."""
        db, _bucket = self._ensure_firebase()
        docs = db.collection(NOTES_COLLECTION).where("sync_status", "==", "uploaded").stream()
        notes: list[dict[str, Any]] = []
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data["mobile_note_id"] = str(data.get("mobile_note_id") or doc.id)
            notes.append(data)
        logger.info("MOBILE_NOTES_IMPORT: notas pendientes=%s", len(notes))
        return notes

    def fetch_note_attachments(self, mobile_note_id: str) -> list[dict[str, Any]]:
        """Read attachment documents for a mobile note."""
        db, _bucket = self._ensure_firebase()
        docs = db.collection(NOTES_COLLECTION).document(mobile_note_id).collection("attachments").stream()
        attachments: list[dict[str, Any]] = []
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data["mobile_attachment_id"] = str(data.get("mobile_attachment_id") or doc.id)
            attachments.append(data)
        return attachments

    def download_attachment(self, storage_path: str, destination_folder: Path | str) -> DownloadedMobileAttachment:
        """Download one Firebase Storage object into the Knowledge internal attachment folder."""
        if not storage_path:
            raise ValueError("El adjunto móvil no tiene storage_path.")
        _db, bucket = self._ensure_firebase()
        destination = Path(destination_folder)
        destination.mkdir(parents=True, exist_ok=True)
        blob = bucket.blob(storage_path)
        filename = Path(storage_path).name or "adjunto_movil"
        local_path = self._unique_path(destination / self._safe_filename(filename))
        logger.info("MOBILE_NOTES_IMPORT: descargando adjunto %s -> %s", storage_path, local_path)
        blob.download_to_filename(str(local_path))
        blob.reload()
        return DownloadedMobileAttachment(
            mobile_attachment_id="",
            original_filename=filename,
            stored_path=local_path,
            storage_path=storage_path,
            mime_type=str(getattr(blob, "content_type", "") or mimetypes.guess_type(filename)[0] or ""),
            file_size=int(getattr(blob, "size", 0) or local_path.stat().st_size),
        )

    def create_knowledge_note_from_mobile(self, note_data: dict[str, Any], attachments: list[DownloadedMobileAttachment]) -> int:
        """Create a local Knowledge note and register downloaded attachments."""
        mobile_note_id = str(note_data.get("mobile_note_id") or "").strip()
        existing = self.conn.execute(
            "SELECT id FROM knowledge_items WHERE source_type = 'mobile' AND source_id = ? AND status != 'deleted' LIMIT 1",
            (mobile_note_id,),
        ).fetchone()
        if existing is not None:
            existing_id = int(existing["id"] if hasattr(existing, "keys") else existing[0])
            logger.info("MOBILE_NOTES_IMPORT: nota móvil ya existía en Knowledge mobile_note_id=%s note_id=%s", mobile_note_id, existing_id)
            return existing_id

        area = str(note_data.get("area") or "")
        topic = str(note_data.get("topic") or "")
        item_id = self.repo.create_item(
            title=str(note_data.get("title") or "Nota móvil").strip() or "Nota móvil",
            content=str(note_data.get("content") or ""),
            area=area,
            topic_id=self._find_topic_id(topic, area),
            tipo=str(note_data.get("type") or note_data.get("tipo") or ""),
            tags=self._normalize_tags(note_data.get("tags")),
            source_type="mobile",
            source_id=mobile_note_id,
            source_path=json.dumps(
                {
                    "user_id": note_data.get("user_id") or "",
                    "device_id": note_data.get("device_id") or "",
                    "mobile_note_id": mobile_note_id,
                    "topic": topic,
                },
                ensure_ascii=False,
            ),
        )
        created_at = self._coerce_datetime_text(note_data.get("created_at"))
        if created_at:
            self.conn.execute("UPDATE knowledge_items SET created_at = ? WHERE id = ?", (created_at, item_id))
            self.conn.commit()
        for attachment in attachments:
            self.repo.add_attachment(
                item_id,
                attachment.original_filename,
                attachment.stored_path.name,
                str(attachment.stored_path),
                attachment.mime_type,
                attachment.file_size,
                source_type="mobile",
            )
        logger.info("MOBILE_NOTES_IMPORT: nota creada en Knowledge mobile_note_id=%s note_id=%s", mobile_note_id, item_id)
        return item_id

    def mark_note_imported(self, mobile_note_id: str) -> None:
        db, _bucket = self._ensure_firebase()
        db.collection(NOTES_COLLECTION).document(mobile_note_id).set(
            {"sync_status": "imported", "imported_at": self._now(), "imported_by_desktop_id": self.desktop_id}, merge=True
        )

    def mark_note_error(self, mobile_note_id: str, error_message: str) -> None:
        db, _bucket = self._ensure_firebase()
        db.collection(NOTES_COLLECTION).document(mobile_note_id).set(
            {"sync_status": "error", "error_message": error_message, "error_at": self._now(), "imported_by_desktop_id": self.desktop_id}, merge=True
        )

    def import_all_pending_notes(self) -> MobileNotesImportSummary:
        start = time.monotonic()
        logger.info("MOBILE_NOTES_IMPORT: inicio importación Firebase → Knowledge")
        self.initialize_firebase()
        notes = self.fetch_pending_notes()
        imported = downloaded = errors = 0
        for note in notes:
            mobile_note_id = str(note.get("mobile_note_id") or "")
            logger.info("MOBILE_NOTES_IMPORT: procesando mobile_note_id=%s", mobile_note_id)
            try:
                attachment_docs = self.fetch_note_attachments(mobile_note_id)
                local_dir = knowledge_attachments_dir() / "mobile" / mobile_note_id
                local_attachments: list[DownloadedMobileAttachment] = []
                for attachment_doc in attachment_docs:
                    storage_path = str(attachment_doc.get("storage_path") or attachment_doc.get("path") or "")
                    downloaded_attachment = self.download_attachment(storage_path, local_dir)
                    local_attachments.append(
                        DownloadedMobileAttachment(
                            mobile_attachment_id=str(attachment_doc.get("mobile_attachment_id") or ""),
                            original_filename=str(attachment_doc.get("filename") or attachment_doc.get("original_filename") or downloaded_attachment.original_filename),
                            stored_path=downloaded_attachment.stored_path,
                            storage_path=storage_path,
                            mime_type=str(attachment_doc.get("mime_type") or downloaded_attachment.mime_type),
                            file_size=int(attachment_doc.get("file_size") or downloaded_attachment.file_size),
                        )
                    )
                    downloaded += 1
                self.create_knowledge_note_from_mobile(note, local_attachments)
                self.mark_note_imported(mobile_note_id)
                imported += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.exception("MOBILE_NOTES_IMPORT: error importando mobile_note_id=%s", mobile_note_id)
                try:
                    self.mark_note_error(mobile_note_id, str(exc))
                except Exception:  # noqa: BLE001
                    logger.exception("MOBILE_NOTES_IMPORT: no se pudo marcar error mobile_note_id=%s", mobile_note_id)
        summary = MobileNotesImportSummary(len(notes), imported, downloaded, errors, time.monotonic() - start)
        logger.info("MOBILE_NOTES_IMPORT: resumen final %s", summary)
        return summary

    def _ensure_firebase(self) -> tuple[Any, Any]:
        if self._db is None or self._bucket is None:
            return self.initialize_firebase()
        return self._db, self._bucket

    def _validate_credentials_file(self) -> None:
        if not self.credentials_path.exists() or not self.credentials_path.is_file():
            raise MobileNotesImportError(f"No se encontró la clave Firebase Admin SDK en: {self.credentials_path}")

    def _credentials_project_id(self) -> str:
        try:
            return str(json.loads(self.credentials_path.read_text(encoding="utf-8")).get("project_id") or "").strip()
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo leer project_id de credenciales Firebase", exc_info=True)
            return ""

    def _find_topic_id(self, topic: str, area: str = "") -> int | None:
        cleaned_topic = topic.strip()
        if not cleaned_topic:
            return None
        cleaned_area = area.strip()
        if cleaned_area:
            row = self.conn.execute(
                """
                SELECT kt.id
                FROM knowledge_topics kt
                LEFT JOIN knowledge_areas ka ON ka.id = kt.area_id
                WHERE kt.name = ? AND COALESCE(NULLIF(kt.area, ''), ka.name, '') = ? AND kt.active = 1
                ORDER BY kt.id ASC
                LIMIT 1
                """,
                (cleaned_topic, cleaned_area),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT id FROM knowledge_topics WHERE name = ? AND active = 1 ORDER BY id ASC LIMIT 1",
                (cleaned_topic,),
            ).fetchone()
        if row is None:
            return None
        return int(row["id"] if hasattr(row, "keys") else row[0])

    @staticmethod
    def _safe_filename(filename: str) -> str:
        import re

        cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).name).strip(" ._")
        return cleaned or "adjunto_movil"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 1
        while True:
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            return [tag.strip() for tag in value.split(",") if tag.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(tag).strip() for tag in value if str(tag).strip()]
        return []

    @staticmethod
    def _coerce_datetime_text(value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "isoformat"):
            return value.isoformat(timespec="seconds") if isinstance(value, datetime) else value.isoformat()
        return str(value).strip()
