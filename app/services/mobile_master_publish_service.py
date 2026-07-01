"""Publish Knowledge Manager master data to Firebase Firestore for mobile clients."""

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
SOURCE = "nexus_desktop"


class MobileMasterPublishError(RuntimeError):
    """Raised when mobile master publication cannot be completed."""


@dataclass(frozen=True)
class MobileMasterPublishSummary:
    """Result counters for a Firebase mobile masters publication run."""

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
            f"Áreas publicadas: {self.areas}\n"
            f"Temas publicados: {self.topics}\n"
            f"Tipos publicados: {self.types}\n"
            f"Etiquetas publicadas: {self.tags}\n"
            f"Errores: {self.errors}\n"
            f"Duración: {self.duration_seconds:.2f} segundos"
            f"{warning_text}"
        )


class MobileMasterPublishService:
    """Publish local Knowledge master values to Firestore without deleting remote data."""

    def __init__(self, conn: sqlite3.Connection, credentials_path: Path | str | None = None):
        self.conn = conn
        self.credentials_path = Path(credentials_path) if credentials_path else DEFAULT_FIREBASE_CREDENTIALS_PATH

    @staticmethod
    def safe_firestore_id(value: object, fallback_prefix: str = "item") -> str:
        """Build a stable Firestore-safe document id from a user-visible name."""
        normalized = unicodedata.normalize("NFKD", str(value or "").strip())
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
        safe = re.sub(r"[^a-z0-9_-]+", "_", ascii_text).strip("_-")
        safe = re.sub(r"_+", "_", safe)
        return safe[:120] or fallback_prefix

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def publish(self) -> MobileMasterPublishSummary:
        """Read local masters and publish them idempotently to Firestore."""
        start = time.monotonic()
        self._validate_credentials_file()
        db = self._firestore_client()
        warnings: list[str] = []
        errors = 0
        logger.info("MOBILE_MASTERS: iniciando publicación hacia Firestore")

        masters = self._load_local_masters(warnings)
        counters = {"areas": 0, "topics": 0, "types": 0, "tags": 0}
        collections = {
            "areas": "areas",
            "topics": "topics",
            "types": "types",
            "tags": "tags",
        }
        for key, items in masters.items():
            collection_name = collections[key]
            for item in items:
                try:
                    db.collection("nexus_masters").document(collection_name).collection("items").document(
                        item["id"]
                    ).set(item, merge=True)
                    counters[key] += 1
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    logger.exception(
                        "MOBILE_MASTERS: error publicando %s/%s", collection_name, item.get("id")
                    )
                    warnings.append(f"No se pudo publicar {collection_name}/{item.get('id')}: {exc}")

        duration = time.monotonic() - start
        summary = MobileMasterPublishSummary(
            areas=counters["areas"],
            topics=counters["topics"],
            types=counters["types"],
            tags=counters["tags"],
            errors=errors,
            duration_seconds=duration,
            warnings=tuple(warnings),
        )
        logger.info("MOBILE_MASTERS: publicación finalizada %s", summary)
        return summary

    def _validate_credentials_file(self) -> None:
        if not self.credentials_path.exists():
            raise MobileMasterPublishError(
                "No se encontró la clave Firebase Admin SDK.\n\n"
                f"Ruta esperada: {self.credentials_path}\n\n"
                "Configura la clave en esa ruta y vuelve a intentarlo."
            )
        if not self.credentials_path.is_file():
            raise MobileMasterPublishError(f"La ruta Firebase no es un archivo JSON válido: {self.credentials_path}")

    def _firestore_client(self) -> Any:
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except ImportError as exc:
            raise MobileMasterPublishError(
                "No está instalada la dependencia firebase-admin. Instala Firebase Admin SDK para publicar maestros."
            ) from exc

        try:
            app_name = "nexus_mobile_masters"
            try:
                app = firebase_admin.get_app(app_name)
            except ValueError:
                cred = credentials.Certificate(str(self.credentials_path))
                app = firebase_admin.initialize_app(cred, name=app_name)
            return firestore.client(app=app)
        except Exception as exc:  # noqa: BLE001
            raise MobileMasterPublishError(f"No se pudo conectar con Firebase Firestore: {exc}") from exc

    def _load_local_masters(self, warnings: list[str]) -> dict[str, list[dict[str, Any]]]:
        masters_repo = MastersRepository(self.conn)
        now = self._now()
        areas = self._masters_rows_to_documents(masters_repo.list_all("Area"), now)
        types = self._masters_rows_to_documents(masters_repo.list_all("Tipo"), now)
        if not areas:
            warnings.append("No se encontraron áreas locales activas/inactivas para publicar.")
        if not types:
            warnings.append("No se encontraron tipos locales activos/inactivos para publicar.")
        topics = self._topic_documents(now, warnings)
        tags = self._tag_documents(now, warnings)
        return {"areas": areas, "topics": topics, "types": types, "tags": tags}

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
            documents.append(
                {
                    "id": doc_id,
                    "name": name,
                    "active": int(row["active"] or 0) == 1,
                    "order": index,
                    "updated_at": now,
                    "source": SOURCE,
                }
            )
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
            documents.append(
                {
                    "id": doc_id,
                    "name": name,
                    "active": int(row["active"] or 0) == 1,
                    "order": int(row["sort_order"] or index),
                    "updated_at": now,
                    "source": SOURCE,
                    "area_id": area_id,
                }
            )
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
            documents.append(
                {
                    "id": doc_id,
                    "name": name,
                    "active": True,
                    "order": index,
                    "updated_at": now,
                    "source": SOURCE,
                }
            )
        return documents
