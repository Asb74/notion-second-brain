"""Import pending Sansebas Nexus Mobile notes from Firebase into local Knowledge."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import sqlite3
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app.config.config_paths import app_data_dir, knowledge_attachments_dir
from app.persistence.knowledge_repository import KnowledgeRepository
from app.services.mobile_firebase_publish_service import DEFAULT_FIREBASE_CREDENTIALS_PATH
from app.services.knowledge_ocr_service import is_image_candidate

logger = logging.getLogger(__name__)

FIREBASE_APP_NAME = "sansebas_nexus_mobile_import"
NOTES_COLLECTION = "nexus_mobile_notes"
DESKTOP_ID = "nexus_desktop"
DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET = "sansebas-nexus.firebasestorage.app"
MOBILE_IMPORT_STORAGE_BUCKET_ENV = "MOBILE_NOTES_IMPORT_STORAGE_BUCKET"


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
class MobileNoteImportErrorDetail:
    """User-facing diagnostic for one failed or partially failed mobile note."""

    mobile_note_id: str
    title: str
    error: str


@dataclass(frozen=True)
class MobileImportAiCandidate:
    """Attachment whose local OCR should be reviewed before optional AI improvement."""

    selected: bool
    mobile_note_id: str
    note_title: str
    attachment_id: int
    filename: str
    local_path: str
    storage_path: str
    ocr_score: int
    reason: str
    ocr_text_preview: str


@dataclass(frozen=True)
class MobileImportedStorageAttachment:
    """Storage object that may be deleted once the user finalizes optional AI review."""

    mobile_note_id: str
    mobile_attachment_id: str
    attachment_id: int
    storage_path: str
    local_path: str
    filename: str


@dataclass(frozen=True)
class MobileNotesImportSummary:
    """Counters for a Firebase → Desktop mobile notes import run."""

    notes_found: int = 0
    notes_imported: int = 0
    notes_with_error: int = 0
    attachments_expected: int = 0
    attachments_found: int = 0
    attachments_downloaded: int = 0
    attachments_deleted: int = 0
    ocr_local_executed: int = 0
    ocr_acceptable: int = 0
    ai_candidates: list[MobileImportAiCandidate] = field(default_factory=list)
    duration_seconds: float = 0.0
    error_details: list[MobileNoteImportErrorDetail] = field(default_factory=list)
    log_path: Path | None = None
    storage_attachments: list[MobileImportedStorageAttachment] = field(default_factory=list)
    delete_storage_after_import: bool = True

    @property
    def errors(self) -> int:
        return self.notes_with_error

    def to_message(self) -> str:
        lines = [
            f"Notas encontradas: {self.notes_found}",
            f"Notas importadas: {self.notes_imported}",
            f"Notas con error: {self.notes_with_error}",
            f"Adjuntos esperados: {self.attachments_expected}",
            f"Adjuntos encontrados: {self.attachments_found}",
            f"Adjuntos descargados: {self.attachments_downloaded}",
            f"Adjuntos borrados de Storage: {self.attachments_deleted}",
            f"OCR local ejecutado: {self.ocr_local_executed}",
            f"OCR aceptable: {self.ocr_acceptable}",
            f"Candidatos a IA: {len(self.ai_candidates)}",
            f"Duración: {self.duration_seconds:.2f} segundos",
        ]
        if self.error_details:
            lines.append("")
            lines.append("Detalle de errores:")
            for detail in self.error_details:
                lines.extend([f"- Nota: {detail.title or 'Nota móvil'}", f"  ID: {detail.mobile_note_id}", f"  Error: {detail.error}"])
        if self.log_path:
            lines.extend(["", f"Log: {self.log_path}"])
        return "\n".join(lines)


class MobileNotesImportService:
    """Import uploaded mobile notes from Firestore and Firebase Storage into Knowledge."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        credentials_path: Path | str | None = None,
        desktop_id: str = DESKTOP_ID,
        storage_bucket_name: str | None = None,
    ):
        self.conn = conn
        self.repo = KnowledgeRepository(conn)
        self.credentials_path = Path(credentials_path) if credentials_path else DEFAULT_FIREBASE_CREDENTIALS_PATH
        self.desktop_id = desktop_id.strip() or DESKTOP_ID
        self.storage_bucket_name = self._resolve_storage_bucket_name(storage_bucket_name)
        self._db: Any | None = None
        self._bucket: Any | None = None
        self._run_logger: logging.Logger = logger
        self._run_handler: logging.Handler | None = None
        self._log_path: Path | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def initialize_firebase(self) -> tuple[Any, Any]:
        """Initialize Firebase Admin SDK and return Firestore and Storage clients."""
        self._run_logger.info("MOBILE_NOTES_IMPORT: inicializando Firebase con ruta %s", self.credentials_path)
        self._validate_credentials_file()
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore, storage
        except ImportError as exc:
            raise MobileNotesImportError(
                "No está instalada la dependencia firebase-admin. Instala Firebase Admin SDK para importar notas móviles."
            ) from exc

        try:
            options: dict[str, str] = {"storageBucket": self.storage_bucket_name}
            try:
                app = firebase_admin.get_app(FIREBASE_APP_NAME)
            except ValueError:
                cred = credentials.Certificate(str(self.credentials_path))
                app = firebase_admin.initialize_app(cred, options=options, name=FIREBASE_APP_NAME)
            self._db = firestore.client(app=app)
            self._bucket = storage.bucket(name=self.storage_bucket_name, app=app)
            bucket_name = getattr(self._bucket, "name", "") or self.storage_bucket_name
            self._run_logger.info("MOBILE_NOTES_IMPORT: Firebase inicializado correctamente bucket=%s", bucket_name)
            return self._db, self._bucket
        except Exception as exc:  # noqa: BLE001
            self._run_logger.exception("MOBILE_NOTES_IMPORT: error inicializando Firebase")
            raise MobileNotesImportError(f"No se pudo conectar con Firebase: {exc}") from exc

    def fetch_pending_notes(self) -> list[dict[str, Any]]:
        db, _bucket = self._ensure_firebase()
        docs = db.collection(NOTES_COLLECTION).where("sync_status", "==", "uploaded").stream()
        notes: list[dict[str, Any]] = []
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data["mobile_note_id"] = str(data.get("mobile_note_id") or doc.id)
            notes.append(data)
        self._run_logger.info("MOBILE_NOTES_IMPORT: notas pendientes=%s", len(notes))
        return notes

    def fetch_note_attachments(self, mobile_note_id: str) -> list[dict[str, Any]]:
        db, _bucket = self._ensure_firebase()
        docs = db.collection(NOTES_COLLECTION).document(mobile_note_id).collection("attachments").stream()
        attachments: list[dict[str, Any]] = []
        for doc in docs:
            data = dict(doc.to_dict() or {})
            data["mobile_attachment_id"] = str(data.get("mobile_attachment_id") or doc.id)
            attachments.append(data)
        self._run_logger.info("MOBILE_NOTES_IMPORT: adjuntos encontrados mobile_note_id=%s count=%s", mobile_note_id, len(attachments))
        return attachments

    def download_attachment(self, storage_path: str, destination_folder: Path | str) -> DownloadedMobileAttachment:
        if not storage_path:
            raise ValueError("El adjunto móvil no tiene storage_path.")
        _db, bucket = self._ensure_firebase()
        normalized_path = self._normalize_storage_path(storage_path, getattr(bucket, "name", ""))
        destination = Path(destination_folder)
        destination.mkdir(parents=True, exist_ok=True)
        blob = bucket.blob(normalized_path)
        filename = Path(normalized_path).name or "adjunto_movil"
        local_path = self._unique_path(destination / self._safe_filename(filename))
        self._run_logger.info("MOBILE_NOTES_IMPORT: descargando adjunto raw=%s normalized=%s destino=%s", storage_path, normalized_path, local_path)
        try:
            blob.download_to_filename(str(local_path))
            blob.reload()
        except Exception as exc:  # noqa: BLE001
            self._run_logger.exception("MOBILE_NOTES_IMPORT: error Storage raw=%s normalized=%s destino=%s", storage_path, normalized_path, local_path)
            raise RuntimeError(self._format_storage_error(exc, normalized_path)) from exc
        return DownloadedMobileAttachment("", filename, local_path, normalized_path, str(getattr(blob, "content_type", "") or mimetypes.guess_type(filename)[0] or ""), int(getattr(blob, "size", 0) or local_path.stat().st_size))

    def create_knowledge_note_from_mobile(self, note_data: dict[str, Any], attachments: list[DownloadedMobileAttachment]) -> tuple[int, list[dict[str, object]]]:
        mobile_note_id = str(note_data.get("mobile_note_id") or "").strip()
        existing = self.conn.execute(
            "SELECT id FROM knowledge_items WHERE source_type = 'mobile' AND source_id = ? AND status != 'deleted' LIMIT 1",
            (mobile_note_id,),
        ).fetchone()
        if existing is not None:
            existing_id = int(existing["id"] if hasattr(existing, "keys") else existing[0])
            self._run_logger.info("MOBILE_NOTES_IMPORT: nota móvil ya existía mobile_note_id=%s note_id=%s", mobile_note_id, existing_id)
            return existing_id, []
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
            source_path=json.dumps({"user_id": note_data.get("user_id") or "", "device_id": note_data.get("device_id") or "", "mobile_note_id": mobile_note_id, "topic": topic}, ensure_ascii=False),
        )
        created_at = self._coerce_datetime_text(note_data.get("created_at"))
        if created_at:
            self.conn.execute("UPDATE knowledge_items SET created_at = ? WHERE id = ?", (created_at, item_id))
            self.conn.commit()
        ocr_results: list[dict[str, object]] = []
        for attachment in attachments:
            attachment_id = self.repo.add_attachment(item_id, attachment.original_filename, attachment.stored_path.name, str(attachment.stored_path), attachment.mime_type, attachment.file_size, source_type="mobile")
            if not is_image_candidate(attachment.stored_path, attachment.mime_type):
                self._run_logger.info("MOBILE_NOTES_IMPORT_OCR: local=%s ocr_executed=no reason=not_image candidate_ai=no", attachment.stored_path)
                continue
            try:
                result = self.repo.ocr_attachment(attachment_id, reindex=False, force=True)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "status": "error", "chars": 0, "message": str(exc), "quality": {"score": 0, "reason": str(exc)}}
            quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
            status = str(result.get("status") or "")
            score = int(float(quality.get("score") or 0))
            reason = str(quality.get("reason") or result.get("message") or status)
            text = str(self.repo.get_attachment(attachment_id)["ocr_text"] or "") if self.repo.get_attachment(attachment_id) is not None else ""
            candidate_ai = status in {"low_quality", "empty", "error", "unavailable"}
            self._run_logger.info(
                "MOBILE_NOTES_IMPORT_OCR: local=%s ocr_executed=yes chars=%s score=%s reason=%s candidate_ai=%s",
                attachment.stored_path, len(text), score, reason, "yes" if candidate_ai else "no",
            )
            ocr_results.append({
                "attachment_id": attachment_id,
                "filename": attachment.original_filename,
                "local_path": str(attachment.stored_path),
                "mobile_attachment_id": attachment.mobile_attachment_id,
                "storage_path": attachment.storage_path,
                "status": status,
                "score": score,
                "reason": reason,
                "text": text,
                "candidate_ai": candidate_ai,
            })
        self.repo.reindex_item(item_id)
        self._run_logger.info("MOBILE_NOTES_IMPORT: nota creada mobile_note_id=%s note_id=%s adjuntos=%s", mobile_note_id, item_id, len(attachments))
        return item_id, ocr_results

    def mark_note_imported(self, mobile_note_id: str) -> None:
        db, _bucket = self._ensure_firebase()
        db.collection(NOTES_COLLECTION).document(mobile_note_id).set({"sync_status": "imported", "imported_at": self._now(), "imported_by_desktop_id": self.desktop_id}, merge=True)

    def mark_note_error(self, mobile_note_id: str, error_message: str) -> None:
        db, _bucket = self._ensure_firebase()
        db.collection(NOTES_COLLECTION).document(mobile_note_id).set({"sync_status": "error", "error_message": error_message[:2000], "error_at": self._now(), "imported_by_desktop_id": self.desktop_id}, merge=True)

    def import_all_pending_notes(self) -> MobileNotesImportSummary:
        start = time.monotonic()
        self._setup_run_logger()
        self._run_logger.info("MOBILE_NOTES_IMPORT: inicio importación Firebase → Knowledge")
        self._run_logger.info("MOBILE_NOTES_IMPORT: credencial usada=%s", self.credentials_path)
        imported = downloaded = errors = expected_total = found_total = ocr_executed = ocr_ok = 0
        ai_candidates: list[MobileImportAiCandidate] = []
        storage_attachments: list[MobileImportedStorageAttachment] = []
        error_details: list[MobileNoteImportErrorDetail] = []
        notes: list[dict[str, Any]] = []
        try:
            self.initialize_firebase()
            notes = self.fetch_pending_notes()
            for note in notes:
                mobile_note_id = str(note.get("mobile_note_id") or "")
                title = str(note.get("title") or "Nota móvil")
                expected = self._coerce_int(note.get("attachments_count"))
                expected_total += expected
                self._run_logger.info("MOBILE_NOTES_IMPORT: nota mobile_note_id=%s title=%s user_id=%s sync_status=%s attachments_count=%s", mobile_note_id, title, note.get("user_id") or "", note.get("sync_status") or "", expected)
                try:
                    attachment_docs = self.fetch_note_attachments(mobile_note_id)
                    found_total += len(attachment_docs)
                    if expected > 0 and not attachment_docs:
                        raise RuntimeError(f"La nota declara attachments_count={expected}, pero la subcolección attachments está vacía.")
                    local_dir = knowledge_attachments_dir() / "mobile" / self._safe_filename(mobile_note_id)
                    local_attachments: list[DownloadedMobileAttachment] = []
                    for attachment_doc in attachment_docs:
                        storage_path = str(attachment_doc.get("storage_path") or attachment_doc.get("path") or "").strip()
                        self._run_logger.info("MOBILE_NOTES_IMPORT: attachment note_id=%s attachment_id=%s storage_path=%s destino_dir=%s", mobile_note_id, attachment_doc.get("mobile_attachment_id") or "", storage_path, local_dir)
                        if not storage_path:
                            raise RuntimeError(f"El documento attachment {attachment_doc.get('mobile_attachment_id') or '(sin id)'} no tiene storage_path.")
                        downloaded_attachment = self.download_attachment(storage_path, local_dir)
                        local_attachments.append(DownloadedMobileAttachment(str(attachment_doc.get("mobile_attachment_id") or ""), str(attachment_doc.get("filename") or attachment_doc.get("original_filename") or downloaded_attachment.original_filename), downloaded_attachment.stored_path, downloaded_attachment.storage_path, str(attachment_doc.get("mime_type") or downloaded_attachment.mime_type), int(attachment_doc.get("file_size") or downloaded_attachment.file_size)))
                        downloaded += 1
                        self._run_logger.info("MOBILE_NOTES_IMPORT: descarga OK attachment_id=%s local=%s", attachment_doc.get("mobile_attachment_id") or "", downloaded_attachment.stored_path)
                    if expected > len(local_attachments):
                        raise RuntimeError(f"La nota esperaba {expected} adjuntos, pero solo se encontraron/descargaron {len(local_attachments)}.")
                    _item_id, ocr_results = self.create_knowledge_note_from_mobile(note, local_attachments)
                    for attachment in local_attachments:
                        row = self.conn.execute(
                            """
                            SELECT id FROM knowledge_attachments
                            WHERE item_id = ? AND stored_path = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (_item_id, str(attachment.stored_path)),
                        ).fetchone()
                        if row is not None:
                            storage_attachments.append(MobileImportedStorageAttachment(
                                mobile_note_id=mobile_note_id,
                                mobile_attachment_id=attachment.mobile_attachment_id,
                                attachment_id=int(row["id"] if hasattr(row, "keys") else row[0]),
                                storage_path=attachment.storage_path,
                                local_path=str(attachment.stored_path),
                                filename=attachment.original_filename,
                            ))
                    for ocr_result in ocr_results:
                        ocr_executed += 1
                        if not bool(ocr_result.get("candidate_ai")):
                            ocr_ok += 1
                        else:
                            ai_candidates.append(MobileImportAiCandidate(
                                selected=True,
                                mobile_note_id=mobile_note_id,
                                note_title=title,
                                attachment_id=int(ocr_result.get("attachment_id") or 0),
                                filename=str(ocr_result.get("filename") or ""),
                                local_path=str(ocr_result.get("local_path") or ""),
                                storage_path=str(ocr_result.get("storage_path") or ""),
                                ocr_score=int(ocr_result.get("score") or 0),
                                reason=str(ocr_result.get("reason") or ""),
                                ocr_text_preview=str(ocr_result.get("text") or "")[:300],
                            ))
                    self.mark_note_imported(mobile_note_id)
                    imported += 1
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    message = str(exc) or exc.__class__.__name__
                    error_details.append(MobileNoteImportErrorDetail(mobile_note_id, title, message))
                    self._run_logger.error("MOBILE_NOTES_IMPORT: error importando mobile_note_id=%s error=%s\n%s", mobile_note_id, message, traceback.format_exc())
                    try:
                        self.mark_note_error(mobile_note_id, message)
                    except Exception:  # noqa: BLE001
                        self._run_logger.exception("MOBILE_NOTES_IMPORT: no se pudo marcar error mobile_note_id=%s", mobile_note_id)
            return MobileNotesImportSummary(len(notes), imported, errors, expected_total, found_total, downloaded, 0, ocr_executed, ocr_ok, ai_candidates, time.monotonic() - start, error_details, self._log_path, storage_attachments)
        finally:
            summary_line = "MOBILE_NOTES_IMPORT: resumen final notes=%s imported=%s errors=%s expected=%s found=%s downloaded=%s duration=%.2f"
            self._run_logger.info(summary_line, len(notes), imported, errors, expected_total, found_total, downloaded, time.monotonic() - start)
            self._teardown_run_logger()

    def finalize_storage_after_import(self, storage_attachments: list[MobileImportedStorageAttachment]) -> tuple[int, int]:
        """Delete imported Firebase Storage objects after the review flow is finalized."""
        if not storage_attachments:
            return 0, 0
        db, bucket = self._ensure_firebase()
        deleted = errors = 0
        for attachment in storage_attachments:
            local_path = Path(attachment.local_path)
            if not local_path.exists():
                errors += 1
                self._run_logger.warning("MOBILE_NOTES_IMPORT_STORAGE_DELETE: omitido sin local attachment_id=%s local=%s", attachment.attachment_id, local_path)
                continue
            try:
                normalized_path = self._normalize_storage_path(attachment.storage_path, getattr(bucket, "name", ""))
                bucket.blob(normalized_path).delete()
                deleted += 1
                self._run_logger.info("MOBILE_NOTES_IMPORT_STORAGE_DELETE: OK attachment_id=%s storage_path=%s", attachment.attachment_id, normalized_path)
                try:
                    db.collection(NOTES_COLLECTION).document(attachment.mobile_note_id).collection("attachments").document(attachment.mobile_attachment_id).set({"deleted": True, "storage_deleted": True, "storage_deleted_at": self._now()}, merge=True)
                except Exception:  # noqa: BLE001
                    self._run_logger.exception("MOBILE_NOTES_IMPORT_STORAGE_DELETE: no se pudo marcar Firestore attachment_id=%s", attachment.attachment_id)
            except Exception:  # noqa: BLE001
                errors += 1
                self._run_logger.exception("MOBILE_NOTES_IMPORT_STORAGE_DELETE: error attachment_id=%s storage_path=%s", attachment.attachment_id, attachment.storage_path)
        return deleted, errors

    def _setup_run_logger(self) -> None:
        logs_dir = app_data_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = logs_dir / f"mobile_import_{datetime.now():%Y%m%d_%H%M%S}.log"
        handler = logging.FileHandler(self._log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        self._run_logger = logging.getLogger(f"{__name__}.run.{id(self)}")
        self._run_logger.setLevel(logging.INFO)
        self._run_logger.propagate = True
        self._run_logger.addHandler(handler)
        self._run_handler = handler

    def _teardown_run_logger(self) -> None:
        if self._run_handler:
            self._run_logger.removeHandler(self._run_handler)
            self._run_handler.close()
            self._run_handler = None

    @staticmethod
    def _normalize_storage_path(storage_path: str, bucket_name: str = "") -> str:
        raw = storage_path.strip()
        if raw.startswith("gs://"):
            parsed = urlparse(raw)
            return unquote(parsed.path.lstrip("/"))
        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"}:
            marker = "/o/"
            if marker in parsed.path:
                return unquote(parsed.path.split(marker, 1)[1].split("/", 1)[0])
            if bucket_name and f"/{bucket_name}/" in parsed.path:
                return unquote(parsed.path.split(f"/{bucket_name}/", 1)[1].lstrip("/"))
            raise ValueError(f"No se pudo interpretar URL de Storage: {storage_path}")
        return unquote(raw.lstrip("/"))

    @staticmethod
    def _format_storage_error(exc: Exception, path: str) -> str:
        code = getattr(exc, "code", "") or getattr(exc, "status_code", "") or getattr(getattr(exc, "response", None), "status_code", "")
        message = str(exc)
        lower = message.lower()
        if "not found" in lower or code == 404:
            firebase_code = "object-not-found"
        elif "permission" in lower or "forbidden" in lower or code in {401, 403}:
            firebase_code = "permission-denied"
        else:
            firebase_code = "storage-error"
        return f"Error descargando adjunto desde Storage ({firebase_code}, código={code or 'desconocido'}): {message}. Ruta: {path}"

    def _ensure_firebase(self) -> tuple[Any, Any]:
        if self._db is None or self._bucket is None:
            return self.initialize_firebase()
        return self._db, self._bucket

    @staticmethod
    def _resolve_storage_bucket_name(storage_bucket_name: str | None = None) -> str:
        return (
            storage_bucket_name
            or os.getenv(MOBILE_IMPORT_STORAGE_BUCKET_ENV)
            or DEFAULT_MOBILE_IMPORT_STORAGE_BUCKET
        ).strip()

    def _validate_credentials_file(self) -> None:
        if not self.credentials_path.exists() or not self.credentials_path.is_file():
            raise MobileNotesImportError(f"No se encontró la clave Firebase Admin SDK en: {self.credentials_path}")

    def _find_topic_id(self, topic: str, area: str = "") -> int | None:
        cleaned_topic = topic.strip()
        if not cleaned_topic:
            return None
        cleaned_area = area.strip()
        if cleaned_area:
            row = self.conn.execute("""
                SELECT kt.id FROM knowledge_topics kt LEFT JOIN knowledge_areas ka ON ka.id = kt.area_id
                WHERE kt.name = ? AND COALESCE(NULLIF(kt.area, ''), ka.name, '') = ? AND kt.active = 1
                ORDER BY kt.id ASC LIMIT 1
                """, (cleaned_topic, cleaned_area)).fetchone()
        else:
            row = self.conn.execute("SELECT id FROM knowledge_topics WHERE name = ? AND active = 1 ORDER BY id ASC LIMIT 1", (cleaned_topic,)).fetchone()
        return None if row is None else int(row["id"] if hasattr(row, "keys") else row[0])

    @staticmethod
    def _safe_filename(filename: str) -> str:
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

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0
