"""Publish Sansebas Nexus Desktop mobile operational data to Firebase Firestore."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.persistence.masters_repository import MastersRepository

logger = logging.getLogger(__name__)

DEFAULT_FIREBASE_CREDENTIALS_PATH = Path(
    r"C:\Firebase Sync\Sansebas Nexus Mobile datos\sansebas-nexus-firebase.json"
)
FIREBASE_APP_NAME = "sansebas_nexus_mobile_publish"
SOURCE = "nexus_desktop"


class MobileFirebasePublishError(RuntimeError):
    """Raised when Firebase publication cannot be completed."""


@dataclass(frozen=True)
class MobileFirebasePublishSummary:
    """Result counters for a Desktop → Firebase mobile publication run."""

    users_published: int = 0
    users_skipped: int = 0
    areas: int = 0
    topics: int = 0
    types: int = 0
    tags: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    warnings: tuple[str, ...] = ()

    def to_message(self) -> str:
        warning_text = ""
        if self.warnings:
            warning_text = "\n\nAvisos:\n" + "\n".join(f"- {warning}" for warning in self.warnings)
        return (
            f"Usuarios publicados: {self.users_published}\n"
            f"Usuarios omitidos: {self.users_skipped}\n"
            f"Áreas publicadas: {self.areas}\n"
            f"Temas publicados: {self.topics}\n"
            f"Tipos publicados: {self.types}\n"
            f"Etiquetas publicadas: {self.tags}\n"
            f"Errores: {self.errors}\n"
            f"Duración: {self.duration_seconds:.2f} segundos"
            f"{warning_text}"
        )


class MobileFirebasePublishService:
    """Publish local mobile users and Knowledge masters to Firestore idempotently."""

    def __init__(self, conn: sqlite3.Connection, credentials_path: Path | str | None = None):
        self.conn = conn
        self.credentials_path = Path(credentials_path) if credentials_path else DEFAULT_FIREBASE_CREDENTIALS_PATH
        self._db: Any | None = None

    @staticmethod
    def safe_firestore_id(value: object, fallback_prefix: str = "item") -> str:
        """Build a stable Firestore-safe document id from a local value."""
        normalized = unicodedata.normalize("NFKD", str(value or "").strip())
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
        safe = re.sub(r"[^a-z0-9_-]+", "_", ascii_text).strip("_-")
        safe = re.sub(r"_+", "_", safe)
        return safe[:120] or fallback_prefix

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def initialize_firebase(self) -> Any:
        """Initialize Firebase Admin SDK and return a Firestore client."""
        logger.info("MOBILE_FIREBASE: inicializando Firebase con ruta %s", self.credentials_path)
        self._validate_credentials_file()
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except ImportError as exc:
            raise MobileFirebasePublishError(
                "No está instalada la dependencia firebase-admin. Instala Firebase Admin SDK para sincronizar datos móviles."
            ) from exc

        try:
            try:
                app = firebase_admin.get_app(FIREBASE_APP_NAME)
            except ValueError:
                cred = credentials.Certificate(str(self.credentials_path))
                app = firebase_admin.initialize_app(cred, name=FIREBASE_APP_NAME)
            self._db = firestore.client(app=app)
            logger.info("MOBILE_FIREBASE: Firebase inicializado correctamente")
            return self._db
        except Exception as exc:  # noqa: BLE001
            logger.exception("MOBILE_FIREBASE: error inicializando Firebase")
            raise MobileFirebasePublishError(f"No se pudo conectar con Firebase Firestore: {exc}") from exc

    def test_connection(self) -> str:
        """Validate credentials and perform a lightweight Firestore read."""
        db = self.initialize_firebase()
        db.collection("nexus_masters").limit(1).get()
        logger.info("MOBILE_FIREBASE: conexión Firestore verificada")
        return "Conexión Firebase correcta."

    def publish_areas(self) -> int:
        return self._publish_master_group("areas", self._masters_rows_to_documents(MastersRepository(self.conn).list_all("Area"), self._now()))

    def publish_topics(self) -> int:
        return self._publish_master_group("topics", self._topic_documents(self._now(), []))

    def publish_types(self) -> int:
        return self._publish_master_group("types", self._masters_rows_to_documents(MastersRepository(self.conn).list_all("Tipo"), self._now()))

    def publish_tags(self) -> int:
        return self._publish_master_group("tags", self._tag_documents(self._now(), []))

    def publish_mobile_users(self) -> tuple[int, int, tuple[str, ...]]:
        """Publish configured mobile users, skipping users without email and never sending passwords."""
        db = self._ensure_db()
        warnings: list[str] = []
        rows = self._load_mobile_user_rows(warnings)
        published = 0
        skipped = 0
        now = self._now()
        for row in rows:
            email = str(row.get("email") or "").strip().lower()
            name = str(row.get("name") or email).strip()
            local_id = row.get("id") or email
            if not email:
                skipped += 1
                warning = f"Usuario móvil omitido sin email: {name or local_id}"
                warnings.append(warning)
                logger.warning("MOBILE_FIREBASE: %s", warning)
                continue
            doc_id = self.safe_firestore_id(local_id or email, "user")
            document = {
                "id": doc_id,
                "name": name,
                "email": email,
                "active": bool(row.get("active", True)),
                "role": str(row.get("role") or "user").strip() or "user",
                "created_at": str(row.get("created_at") or now),
                "updated_at": now,
                "source": SOURCE,
            }
            db.collection("nexus_mobile_users").document(doc_id).set(document, merge=True)
            published += 1
        logger.info("MOBILE_FIREBASE: usuarios publicados=%s omitidos=%s", published, skipped)
        return published, skipped, tuple(warnings)

    def publish_all(self) -> MobileFirebasePublishSummary:
        """Publish all mobile users and Knowledge masters without deleting remote documents."""
        start = time.monotonic()
        warnings: list[str] = []
        errors = 0
        logger.info("MOBILE_FIREBASE: inicio sincronización Desktop → Firebase")
        logger.info("MOBILE_FIREBASE: ruta credenciales %s", self.credentials_path)
        self.initialize_firebase()

        counters = {"areas": 0, "topics": 0, "types": 0, "tags": 0}
        users_published = 0
        users_skipped = 0
        operations = (
            ("areas", lambda: self.publish_areas()),
            ("topics", lambda: self.publish_topics()),
            ("types", lambda: self.publish_types()),
            ("tags", lambda: self.publish_tags()),
        )
        for key, operation in operations:
            try:
                counters[key] = operation()
                logger.info("MOBILE_FIREBASE: grupo %s publicado=%s", key, counters[key])
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.exception("MOBILE_FIREBASE: error publicando grupo %s", key)
                warnings.append(f"No se pudo publicar {key}: {exc}")
        try:
            users_published, users_skipped, user_warnings = self.publish_mobile_users()
            warnings.extend(user_warnings)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.exception("MOBILE_FIREBASE: error publicando usuarios móviles")
            warnings.append(f"No se pudieron publicar usuarios móviles: {exc}")

        summary = MobileFirebasePublishSummary(
            users_published=users_published,
            users_skipped=users_skipped,
            areas=counters["areas"],
            topics=counters["topics"],
            types=counters["types"],
            tags=counters["tags"],
            errors=errors,
            duration_seconds=time.monotonic() - start,
            warnings=tuple(warnings),
        )
        logger.info("MOBILE_FIREBASE: resumen final %s", summary)
        return summary

    def _validate_credentials_file(self) -> None:
        if not self.credentials_path.exists():
            raise MobileFirebasePublishError(
                "No se encontró la clave Firebase Admin SDK.\n\n"
                f"Ruta esperada: {self.credentials_path}\n\n"
                "Configura la clave en esa ruta y vuelve a intentarlo."
            )
        if not self.credentials_path.is_file():
            raise MobileFirebasePublishError(f"La ruta Firebase no es un archivo JSON válido: {self.credentials_path}")

    def _ensure_db(self) -> Any:
        return self._db if self._db is not None else self.initialize_firebase()

    def _publish_master_group(self, group: str, documents: list[dict[str, Any]]) -> int:
        db = self._ensure_db()
        for document in documents:
            db.collection("nexus_masters").document(group).collection("items").document(document["id"]).set(
                document, merge=True
            )
        return len(documents)

    def _masters_rows_to_documents(self, rows: list[sqlite3.Row], now: str) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            name = str(row["value"] or "").strip()
            if not name:
                continue
            doc_id = self.safe_firestore_id(name)
            if doc_id in seen:
                doc_id = f"{doc_id}_{int(row['id'])}"
            seen.add(doc_id)
            documents.append({"id": doc_id, "name": name, "active": int(row["active"] or 0) == 1, "order": index, "updated_at": now, "source": SOURCE})
        return documents

    def _topic_documents(self, now: str, warnings: list[str]) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT kt.id, kt.name, kt.active, kt.sort_order,
                   COALESCE(NULLIF(kt.area, ''), ka.name) AS area_name
            FROM knowledge_topics kt
            LEFT JOIN knowledge_areas ka ON ka.id = kt.area_id
            ORDER BY area_name COLLATE NOCASE ASC, kt.sort_order ASC, kt.name COLLATE NOCASE ASC
            """
        ).fetchall()
        if not rows:
            warnings.append("No se encontraron temas locales para publicar.")
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            name = str(row["name"] or "").strip()
            if not name:
                continue
            area_id = self.safe_firestore_id(row["area_name"] or "sin_area", "sin_area")
            doc_id = self.safe_firestore_id(f"{area_id}_{name}")
            if doc_id in seen:
                doc_id = f"{doc_id}_{int(row['id'])}"
            seen.add(doc_id)
            documents.append({"id": doc_id, "name": name, "active": int(row["active"] or 0) == 1, "order": int(row["sort_order"] or index), "updated_at": now, "source": SOURCE, "area_id": area_id})
        return documents

    def _tag_documents(self, now: str, warnings: list[str]) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT kt.id, kt.name, COUNT(kit.item_id) AS usage_count
            FROM knowledge_tags kt
            LEFT JOIN knowledge_item_tags kit ON kit.tag_id = kt.id
            GROUP BY kt.id, kt.name
            ORDER BY usage_count DESC, kt.name COLLATE NOCASE ASC
            """
        ).fetchall()
        if not rows:
            warnings.append("No se encontraron etiquetas locales frecuentes para publicar.")
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            name = str(row["name"] or "").strip()
            if not name:
                continue
            doc_id = self.safe_firestore_id(name)
            if doc_id in seen:
                doc_id = f"{doc_id}_{int(row['id'])}"
            seen.add(doc_id)
            documents.append({"id": doc_id, "name": name, "active": True, "order": index, "updated_at": now, "source": SOURCE})
        return documents

    def _load_mobile_user_rows(self, warnings: list[str]) -> list[dict[str, Any]]:
        tables = {str(row["name"]) for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        if "mobile_users" not in tables:
            # TODO: conectar esta lectura con la futura tabla/configuración local de usuarios móviles autorizados.
            warning = "No existe tabla local mobile_users; no se publican usuarios móviles en esta fase."
            warnings.append(warning)
            logger.warning("MOBILE_FIREBASE: %s", warning)
            return []
        columns = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(mobile_users)").fetchall()}
        select_id = "id" if "id" in columns else "rowid"
        select_name = "name" if "name" in columns else "''"
        select_email = "email" if "email" in columns else "''"
        select_active = "active" if "active" in columns else "1"
        select_role = "role" if "role" in columns else "'user'"
        select_created = "created_at" if "created_at" in columns else "''"
        rows = self.conn.execute(
            f"SELECT {select_id} AS id, {select_name} AS name, {select_email} AS email, {select_active} AS active, {select_role} AS role, {select_created} AS created_at FROM mobile_users"
        ).fetchall()
        return [dict(row) for row in rows]
