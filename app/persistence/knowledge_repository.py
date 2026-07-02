"""SQLite repository for the Knowledge Manager module."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

from app.services.knowledge_indexer_service import extract_text_from_attachment, index_note

logger = logging.getLogger(__name__)


def _fallback_normalize_search_value(value: object) -> str:
    return str(value or "").casefold()


class KnowledgeRepository:
    """Data access layer for generic knowledge items, areas, types, and tags."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _ensure_normalized_search_function(self) -> bool:
        """Register a SQLite helper for accent-insensitive local Knowledge search."""
        try:
            from app.services.knowledge_query_service import normalize_text

            self.conn.create_function("KNOWLEDGE_NORMALIZE", 1, normalize_text)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo registrar normalización SQL de Knowledge", exc_info=True)
            return False
        return True

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        result: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            cleaned = str(tag or "").strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                result.append(cleaned)
                seen.add(key)
        return result

    def list_areas(self, active_only: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM knowledge_areas"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY sort_order ASC, name COLLATE NOCASE ASC"
        return self.conn.execute(query).fetchall()

    def create_area(self, name: str, description: str = "", color: str = "") -> int:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("El nombre del área no puede estar vacío")
        now = self._now()
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_areas(name, description, color, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cleaned, description.strip(), color.strip(), now, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_area(
        self,
        area_id: int,
        name: str,
        description: str = "",
        color: str = "",
        active: bool = True,
    ) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("El nombre del área no puede estar vacío")
        self.conn.execute(
            """
            UPDATE knowledge_areas
            SET name = ?, description = ?, color = ?, active = ?, updated_at = ?
            WHERE id = ?
            """,
            (cleaned, description.strip(), color.strip(), int(active), self._now(), area_id),
        )
        self.conn.commit()

    def list_topics(
        self,
        area: str | None = None,
        active_only: bool = True,
        area_id: int | None = None,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT kt.*, COALESCE(NULLIF(kt.area, ''), ka.name) AS area_name
            FROM knowledge_topics kt
            LEFT JOIN knowledge_areas ka ON ka.id = kt.area_id
        """
        clauses: list[str] = []
        params: list[object] = []
        if isinstance(area, int):
            area_id = area
            area = None
        cleaned_area = (area or "").strip()
        if cleaned_area:
            clauses.append("COALESCE(NULLIF(kt.area, ''), ka.name) = ?")
            params.append(cleaned_area)
        elif area_id is not None:
            clauses.append("kt.area_id = ?")
            params.append(area_id)
        if active_only:
            clauses.append("kt.active = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY area_name COLLATE NOCASE ASC, kt.sort_order ASC, kt.name COLLATE NOCASE ASC"
        return self.conn.execute(query, tuple(params)).fetchall()

    def create_topic(
        self,
        name: str,
        area: str | None = None,
        description: str = "",
        area_id: int | None = None,
    ) -> int:
        if isinstance(area, int):
            area_id = area
            area = None
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("El nombre del tema no puede estar vacío")
        cleaned_area = (area or "").strip() or self._legacy_area_name(area_id)
        now = self._now()
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_topics(name, area_id, area, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cleaned, area_id, cleaned_area, description.strip(), now, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_topic(
        self,
        topic_id: int,
        name: str,
        area: str | None = None,
        description: str = "",
        active: bool = True,
        area_id: int | None = None,
    ) -> None:
        if isinstance(area, int):
            area_id = area
            area = None
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("El nombre del tema no puede estar vacío")
        cleaned_area = (area or "").strip() or self._legacy_area_name(area_id)
        self.conn.execute(
            """
            UPDATE knowledge_topics
            SET name = ?, area_id = ?, area = ?, description = ?, active = ?, updated_at = ?
            WHERE id = ?
            """,
            (cleaned, area_id, cleaned_area, description.strip(), int(active), self._now(), topic_id),
        )
        self.conn.commit()

    def list_item_types(self, active_only: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM knowledge_item_types"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY name COLLATE NOCASE ASC"
        return self.conn.execute(query).fetchall()

    def create_item_type(self, name: str, description: str = "", icon: str = "") -> int:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("El nombre del tipo no puede estar vacío")
        now = self._now()
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_item_types(name, description, icon, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cleaned, description.strip(), icon.strip(), now, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_item_type(
        self,
        type_id: int,
        name: str,
        description: str = "",
        icon: str = "",
        active: bool = True,
    ) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("El nombre del tipo no puede estar vacío")
        self.conn.execute(
            """
            UPDATE knowledge_item_types
            SET name = ?, description = ?, icon = ?, active = ?, updated_at = ?
            WHERE id = ?
            """,
            (cleaned, description.strip(), icon.strip(), int(active), self._now(), type_id),
        )
        self.conn.commit()

    def _legacy_area_name(self, area_id: int | None) -> str:
        if area_id is None:
            return ""
        row = self.conn.execute("SELECT name FROM knowledge_areas WHERE id = ?", (area_id,)).fetchone()
        return str(row["name"] or "") if row else ""

    def _legacy_type_name(self, item_type_id: int | None) -> str:
        if item_type_id is None:
            return ""
        row = self.conn.execute("SELECT name FROM knowledge_item_types WHERE id = ?", (item_type_id,)).fetchone()
        return str(row["name"] or "") if row else ""

    def create_item(
        self,
        title: str,
        content: str,
        area_id: int | None = None,
        item_type_id: int | None = None,
        tags: list[str] | None = None,
        source_type: str = "manual",
        source_id: str = "",
        source_path: str = "",
        summary: str = "",
        topic_id: int | None = None,
        area: str = "",
        tipo: str = "",
    ) -> int:
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("El título no puede estar vacío")
        cleaned_area = area.strip() or self._legacy_area_name(area_id)
        cleaned_tipo = tipo.strip() or self._legacy_type_name(item_type_id)
        cleaned_source_type = source_type.strip() or "manual"
        cleaned_summary = self._summary_for_new_item(cleaned_source_type, summary)
        now = self._now()
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_items(
                title, content, summary, area_id, area, topic_id, item_type_id, tipo, source_type,
                source_id, source_path, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                cleaned_title,
                content,
                cleaned_summary,
                area_id,
                cleaned_area,
                topic_id,
                item_type_id,
                cleaned_tipo,
                cleaned_source_type,
                source_id.strip(),
                source_path.strip(),
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
        self.set_tags_for_item(item_id, tags or [])
        self.conn.commit()
        self.reindex_item(item_id)
        return item_id

    @staticmethod
    def _summary_for_new_item(source_type: str, summary: str) -> str:
        """Normalize summaries for newly created Knowledge items.

        Automatic ingestion sources must leave ``summary`` empty. Existing
        rows are not modified, and later user edits still go through
        ``update_item`` unchanged.
        """
        automatic_sources = {
            "evernote",
            "email",
            "document",
            "documents",
            "documento",
            "documentos",
            "pdf",
            "docx",
            "file",
            "archivo",
            "attachment",
            "adjunto",
            "import",
        }
        if source_type.strip().lower() in automatic_sources:
            return ""
        return summary

    def update_item_summary(self, item_id: int, summary: str) -> None:
        """Persist only the Knowledge summary for on-demand AI generation or user action."""
        self.conn.execute(
            """
            UPDATE knowledge_items
            SET summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, self._now(), item_id),
        )
        self.conn.commit()
        self.reindex_item(item_id)

    def update_item(
        self,
        item_id: int,
        title: str,
        content: str,
        area_id: int | None = None,
        item_type_id: int | None = None,
        tags: list[str] | None = None,
        summary: str = "",
        status: str = "active",
        topic_id: int | None = None,
        area: str = "",
        tipo: str = "",
    ) -> None:
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("El título no puede estar vacío")
        cleaned_area = area.strip() or self._legacy_area_name(area_id)
        cleaned_tipo = tipo.strip() or self._legacy_type_name(item_type_id)
        self.conn.execute(
            """
            UPDATE knowledge_items
            SET title = ?, content = ?, summary = ?, area_id = ?, area = ?, topic_id = ?,
                item_type_id = ?, tipo = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                cleaned_title,
                content,
                summary,
                area_id,
                cleaned_area,
                topic_id,
                item_type_id,
                cleaned_tipo,
                status.strip() or "active",
                self._now(),
                item_id,
            ),
        )
        self.set_tags_for_item(item_id, tags or [])
        self.conn.commit()
        self.reindex_item(item_id)

    def get_item(self, item_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT ki.*,
                   COALESCE(NULLIF(ki.area, ''), ka.name) AS area_name,
                   kt.name AS topic_name,
                   COALESCE(NULLIF(ki.tipo, ''), kit.name) AS item_type_name
            FROM knowledge_items ki
            LEFT JOIN knowledge_areas ka ON ka.id = ki.area_id
            LEFT JOIN knowledge_topics kt ON kt.id = ki.topic_id
            LEFT JOIN knowledge_item_types kit ON kit.id = ki.item_type_id
            WHERE ki.id = ?
            """,
            (item_id,),
        ).fetchone()

    def list_items(
        self,
        search: str = "",
        area_id: int | None = None,
        item_type_id: int | None = None,
        limit: int = 500,
        topic_id: int | None = None,
        area: str | None = None,
        tipo: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["ki.status != 'deleted'"]
        params: list[object] = []
        cleaned_search = search.strip()
        if cleaned_search:
            try:
                from app.services.knowledge_query_service import extract_phrases, extract_terms, normalize_text

                search_terms = [*extract_phrases(cleaned_search), *extract_terms(cleaned_search)]
                normalize_search_value = normalize_text
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo normalizar la búsqueda de Knowledge Manager", exc_info=True)
                search_terms = []
                normalize_search_value = _fallback_normalize_search_value
            if not search_terms:
                search_terms = [cleaned_search]
            use_normalized_sql = self._ensure_normalized_search_function()
            normalized_clauses: list[str] = []
            for term in search_terms:
                like = f"%{term}%"
                if use_normalized_sql:
                    normalized_like = f"%{normalize_search_value(term)}%"
                    normalized_clauses.append(
                        "(ki.title LIKE ? OR KNOWLEDGE_NORMALIZE(ki.title) LIKE ? OR "
                        "ki.content LIKE ? OR KNOWLEDGE_NORMALIZE(ki.content) LIKE ? OR "
                        "ki.summary LIKE ? OR KNOWLEDGE_NORMALIZE(ki.summary) LIKE ? OR "
                        "COALESCE(NULLIF(ki.area, ''), ka.name) LIKE ? OR "
                        "KNOWLEDGE_NORMALIZE(COALESCE(NULLIF(ki.area, ''), ka.name)) LIKE ? OR "
                        "kt.name LIKE ? OR KNOWLEDGE_NORMALIZE(kt.name) LIKE ? OR "
                        "COALESCE(NULLIF(ki.tipo, ''), kit.name) LIKE ? OR "
                        "KNOWLEDGE_NORMALIZE(COALESCE(NULLIF(ki.tipo, ''), kit.name)) LIKE ? OR "
                        "ki.source_type LIKE ? OR KNOWLEDGE_NORMALIZE(ki.source_type) LIKE ? OR "
                        "ki.source_id LIKE ? OR KNOWLEDGE_NORMALIZE(ki.source_id) LIKE ? OR "
                        "ki.source_path LIKE ? OR KNOWLEDGE_NORMALIZE(ki.source_path) LIKE ? OR "
                        "ki.indexed_text LIKE ? OR KNOWLEDGE_NORMALIZE(ki.indexed_text) LIKE ? OR "
                        "EXISTS (SELECT 1 FROM knowledge_item_tags kit2 "
                        "JOIN knowledge_tags kt2 ON kt2.id = kit2.tag_id "
                        "WHERE kit2.item_id = ki.id AND "
                        "(kt2.name LIKE ? OR KNOWLEDGE_NORMALIZE(kt2.name) LIKE ?)))"
                    )
                    params.extend([like, normalized_like] * 11)
                else:
                    normalized_clauses.append(
                        "(ki.title LIKE ? OR ki.content LIKE ? OR ki.summary LIKE ? OR "
                        "COALESCE(NULLIF(ki.area, ''), ka.name) LIKE ? OR "
                        "kt.name LIKE ? OR "
                        "COALESCE(NULLIF(ki.tipo, ''), kit.name) LIKE ? OR "
                        "ki.source_type LIKE ? OR ki.source_id LIKE ? OR ki.source_path LIKE ? OR "
                        "ki.indexed_text LIKE ? OR "
                        "EXISTS (SELECT 1 FROM knowledge_item_tags kit2 "
                        "JOIN knowledge_tags kt2 ON kt2.id = kit2.tag_id "
                        "WHERE kit2.item_id = ki.id AND kt2.name LIKE ?))"
                    )
                    params.extend([like, like, like, like, like, like, like, like, like, like, like])
            clauses.append("(" + " OR ".join(normalized_clauses) + ")")
        cleaned_area = (area or "").strip()
        cleaned_tipo = (tipo or "").strip()
        if cleaned_area:
            clauses.append("COALESCE(NULLIF(ki.area, ''), ka.name) = ?")
            params.append(cleaned_area)
        elif area_id is not None:
            clauses.append("ki.area_id = ?")
            params.append(area_id)
        if cleaned_tipo:
            clauses.append("COALESCE(NULLIF(ki.tipo, ''), kit.name) = ?")
            params.append(cleaned_tipo)
        elif item_type_id is not None:
            clauses.append("ki.item_type_id = ?")
            params.append(item_type_id)
        if topic_id is not None:
            clauses.append("ki.topic_id = ?")
            params.append(topic_id)
        params.append(max(1, int(limit)))
        return self.conn.execute(
            f"""
            SELECT ki.id, ki.title, ki.source_type, ki.updated_at, ki.created_at,
                   COALESCE(NULLIF(ki.area, ''), ka.name) AS area_name,
                   kt.name AS topic_name,
                   COALESCE(NULLIF(ki.tipo, ''), kit.name) AS item_type_name,
                   ki.indexed_text
            FROM knowledge_items ki
            LEFT JOIN knowledge_areas ka ON ka.id = ki.area_id
            LEFT JOIN knowledge_topics kt ON kt.id = ki.topic_id
            LEFT JOIN knowledge_item_types kit ON kit.id = ki.item_type_id
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(ki.updated_at, ki.created_at) DESC, ki.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    def search_query_candidates(self, terms: list[str], limit: int = 200) -> list[sqlite3.Row]:
        """Return Knowledge notes that may match natural-language local queries."""
        cleaned_terms = [str(term or "").strip() for term in terms if str(term or "").strip()]
        if not cleaned_terms:
            return []

        try:
            from app.services.knowledge_query_service import normalize_text
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo normalizar candidatos de consulta Knowledge", exc_info=True)
            normalize_text = _fallback_normalize_search_value
        use_normalized_sql = self._ensure_normalized_search_function()

        clauses = ["ki.status != 'deleted'"]
        params: list[object] = []
        term_clauses: list[str] = []
        for term in cleaned_terms:
            like = f"%{term}%"
            if use_normalized_sql:
                normalized_like = f"%{normalize_text(term)}%"
                term_clauses.append(
                    "("
                    "ki.title LIKE ? OR KNOWLEDGE_NORMALIZE(ki.title) LIKE ? OR "
                    "ki.content LIKE ? OR KNOWLEDGE_NORMALIZE(ki.content) LIKE ? OR "
                    "ki.summary LIKE ? OR KNOWLEDGE_NORMALIZE(ki.summary) LIKE ? OR "
                    "ki.indexed_text LIKE ? OR KNOWLEDGE_NORMALIZE(ki.indexed_text) LIKE ? OR "
                    "COALESCE(NULLIF(ki.area, ''), ka.name) LIKE ? OR "
                    "KNOWLEDGE_NORMALIZE(COALESCE(NULLIF(ki.area, ''), ka.name)) LIKE ? OR "
                    "kt.name LIKE ? OR KNOWLEDGE_NORMALIZE(kt.name) LIKE ? OR "
                    "COALESCE(NULLIF(ki.tipo, ''), kit.name) LIKE ? OR "
                    "KNOWLEDGE_NORMALIZE(COALESCE(NULLIF(ki.tipo, ''), kit.name)) LIKE ? OR "
                    "EXISTS (SELECT 1 FROM knowledge_item_tags ktag_link "
                    "JOIN knowledge_tags ktag ON ktag.id = ktag_link.tag_id "
                    "WHERE ktag_link.item_id = ki.id AND "
                    "(ktag.name LIKE ? OR KNOWLEDGE_NORMALIZE(ktag.name) LIKE ?)) OR "
                    "EXISTS (SELECT 1 FROM knowledge_attachments katt "
                    "WHERE katt.item_id = ki.id AND "
                    "(katt.original_filename LIKE ? OR KNOWLEDGE_NORMALIZE(katt.original_filename) LIKE ? OR "
                    "katt.stored_filename LIKE ? OR KNOWLEDGE_NORMALIZE(katt.stored_filename) LIKE ? OR "
                    "katt.stored_path LIKE ? OR KNOWLEDGE_NORMALIZE(katt.stored_path) LIKE ?))"
                    ")"
                )
                params.extend([like, normalized_like] * 11)
            else:
                term_clauses.append(
                    "("
                    "ki.title LIKE ? OR ki.content LIKE ? OR ki.summary LIKE ? OR ki.indexed_text LIKE ? OR "
                    "COALESCE(NULLIF(ki.area, ''), ka.name) LIKE ? OR kt.name LIKE ? OR "
                    "COALESCE(NULLIF(ki.tipo, ''), kit.name) LIKE ? OR "
                    "EXISTS (SELECT 1 FROM knowledge_item_tags ktag_link "
                    "JOIN knowledge_tags ktag ON ktag.id = ktag_link.tag_id "
                    "WHERE ktag_link.item_id = ki.id AND ktag.name LIKE ?) OR "
                    "EXISTS (SELECT 1 FROM knowledge_attachments katt "
                    "WHERE katt.item_id = ki.id AND "
                    "(katt.original_filename LIKE ? OR katt.stored_filename LIKE ? OR katt.stored_path LIKE ?))"
                    ")"
                )
                params.extend([like, like, like, like, like, like, like, like, like, like, like])
        clauses.append("(" + " OR ".join(term_clauses) + ")")
        params.append(max(1, int(limit)))
        return self.conn.execute(
            f"""
            SELECT
                   ki.id AS note_id,
                   ki.title,
                   ki.content,
                   ki.summary,
                   ki.indexed_text,
                   ki.source_type,
                   COALESCE(NULLIF(ki.area, ''), ka.name) AS area,
                   kt.name AS topic,
                   COALESCE(NULLIF(ki.tipo, ''), kit.name) AS type,
                   COALESCE((
                       SELECT GROUP_CONCAT(ktag.name, ', ')
                       FROM knowledge_item_tags ktag_link
                       JOIN knowledge_tags ktag ON ktag.id = ktag_link.tag_id
                       WHERE ktag_link.item_id = ki.id
                   ), '') AS tags,
                   COALESCE((
                       SELECT GROUP_CONCAT(
                           katt.original_filename || ' ' || katt.stored_filename || ' ' || katt.stored_path,
                           ' '
                       )
                       FROM knowledge_attachments katt
                       WHERE katt.item_id = ki.id
                   ), '') AS attachment_names,
                   ki.updated_at,
                   ki.created_at
            FROM knowledge_items ki
            LEFT JOIN knowledge_areas ka ON ka.id = ki.area_id
            LEFT JOIN knowledge_topics kt ON kt.id = ki.topic_id
            LEFT JOIN knowledge_item_types kit ON kit.id = ki.item_type_id
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(ki.updated_at, ki.created_at) DESC, ki.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()



    def exists_evernote_duplicate(self, title: str, created: str = "") -> bool:
        """Return True for the pilot Evernote duplicate rule: source + title + created date."""
        cleaned_title = title.strip()
        cleaned_created = created.strip()
        if not cleaned_title:
            return False
        row = self.conn.execute(
            """
            SELECT 1
            FROM knowledge_items
            WHERE source_type = 'evernote'
              AND title = ?
              AND COALESCE(source_id, '') = ?
              AND status != 'deleted'
            LIMIT 1
            """,
            (cleaned_title, cleaned_created),
        ).fetchone()
        return row is not None

    def delete_item(self, item_id: int) -> None:
        self.conn.execute("DELETE FROM knowledge_entity_links WHERE note_id = ?", (item_id,))
        self.conn.execute("DELETE FROM knowledge_item_tags WHERE item_id = ?", (item_id,))
        self.conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
        self.conn.commit()

    def get_tags_for_item(self, item_id: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT kt.name
            FROM knowledge_tags kt
            JOIN knowledge_item_tags kit ON kit.tag_id = kt.id
            WHERE kit.item_id = ?
            ORDER BY kt.name COLLATE NOCASE ASC
            """,
            (item_id,),
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def set_tags_for_item(self, item_id: int, tags: list[str]) -> None:
        normalized = self._normalize_tags(tags)
        self.conn.execute("DELETE FROM knowledge_item_tags WHERE item_id = ?", (item_id,))
        now = self._now()
        for tag in normalized:
            self.conn.execute(
                """
                INSERT INTO knowledge_tags(name, created_at)
                VALUES (?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (tag, now),
            )
            row = self.conn.execute("SELECT id FROM knowledge_tags WHERE name = ?", (tag,)).fetchone()
            if row is None:
                continue
            self.conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_item_tags(item_id, tag_id)
                VALUES (?, ?)
                """,
                (item_id, int(row["id"])),
            )

    def add_attachment(
        self,
        item_id: int,
        original_filename: str,
        stored_filename: str,
        stored_path: str,
        mime_type: str = "",
        file_size: int = 0,
        source_type: str = "manual",
    ) -> int:
        now = self._now()
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_attachments(
                item_id, original_filename, stored_filename, stored_path,
                mime_type, file_size, source_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                original_filename.strip(),
                stored_filename.strip(),
                stored_path.strip(),
                mime_type.strip(),
                int(file_size or 0),
                source_type.strip() or "manual",
                now,
                now,
            ),
        )
        self.conn.commit()
        attachment_id = int(cursor.lastrowid)
        self._run_automatic_attachment_ocr(attachment_id, item_id)
        self.reindex_item(item_id)
        return attachment_id

    def _run_automatic_attachment_ocr(self, attachment_id: int, item_id: int) -> None:
        """Run the shared OCR pipeline for every new OCR-capable attachment source."""
        try:
            row = self.get_attachment(attachment_id)
            if row is None:
                return
            from app.services.knowledge_ocr_service import should_ocr_attachment

            path = str(row["stored_path"] or "")
            mime = str(row["mime_type"] or "")
            if not should_ocr_attachment(path, mime):
                logger.info("KNOWLEDGE_OCR_PIPELINE: auto skipped attachment_id=%s source=%s reason=not_candidate", attachment_id, row["source_type"] if "source_type" in row.keys() else "")
                return
            logger.info("KNOWLEDGE_OCR_PIPELINE: auto started attachment_id=%s source=%s", attachment_id, row["source_type"] if "source_type" in row.keys() else "")
            self.ocr_attachment(attachment_id, reindex=False, force=False)
        except Exception as exc:  # noqa: BLE001
            logger.info("KNOWLEDGE_OCR_PIPELINE: auto failed note_id=%s attachment_id=%s reason=%s", item_id, attachment_id, exc)

    def list_attachments(self, item_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM knowledge_attachments
            WHERE item_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (item_id,),
        ).fetchall()

    def get_attachment(self, attachment_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM knowledge_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()

    def delete_attachment(self, attachment_id: int) -> None:
        row = self.get_attachment(attachment_id)
        item_id = int(row["item_id"]) if row is not None else None
        self.conn.execute("DELETE FROM knowledge_attachments WHERE id = ?", (attachment_id,))
        self.conn.commit()
        if item_id is not None:
            self.reindex_item(item_id)

    def update_attachment_ocr(
        self,
        attachment_id: int,
        ocr_text: str = "",
        ocr_status: str = "",
        *,
        commit: bool = True,
        ocr_mode: str = "",
        ocr_rotation: int | None = None,
        ocr_characters: int | None = None,
    ) -> None:
        now = self._now()
        cursor = self.conn.execute(
            """
            UPDATE knowledge_attachments
            SET ocr_text = ?, ocr_text_raw = ?, ocr_status = ?, ocr_updated_at = ?, updated_at = ?,
                ocr_mode = ?, ocr_engine = ?, ocr_rotation = ?, ocr_characters = ?, ocr_quality_score = NULL, ocr_quality_reason = NULL
            WHERE id = ?
            """,
            (
                ocr_text,
                ocr_text,
                ocr_status,
                now,
                now,
                ocr_mode,
                "local" if ocr_status in {"ok", "ok_local", "low_quality", "empty"} else "",
                ocr_rotation,
                ocr_characters if ocr_characters is not None else len(ocr_text),
                attachment_id,
            ),
        )
        if commit:
            self.conn.commit()
        logger.info("KNOWLEDGE_OCR_PERSIST: table=knowledge_attachments fields=ocr_text,ocr_text_raw,ocr_status attachment_id=%s rows=%s status=%s chars=%s", attachment_id, cursor.rowcount, ocr_status, len(ocr_text))

    def save_attachment_ocr_correction(self, attachment_id: int, corrected_text: str) -> dict[str, object]:
        row = self.get_attachment(attachment_id)
        if row is None:
            return {"ok": False, "message": "Adjunto no encontrado."}
        now = self._now()
        item_id = int(row["item_id"])
        self.conn.execute(
            """
            UPDATE knowledge_attachments
            SET ocr_text_corrected = ?, ocr_corrected_at = ?, ocr_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (corrected_text, now, "corrected", now, attachment_id),
        )
        self.conn.commit()
        logger.info("KNOWLEDGE_OCR: correction saved attachment_id=%s chars=%s", attachment_id, len(corrected_text))
        logger.info("KNOWLEDGE_OCR: reindex after correction note_id=%s", item_id)
        self.reindex_item(item_id)
        return {"ok": True, "item_id": item_id, "chars": len(corrected_text)}

    def ocr_attachment(
        self,
        attachment_id: int,
        *,
        reindex: bool = True,
        force: bool = False,
        max_pdf_pages: int = 5,
    ) -> dict[str, object]:
        row = self.get_attachment(attachment_id)
        if row is None:
            return {"ok": False, "status": "error", "chars": 0, "message": "Adjunto no encontrado."}
        path = str(row["stored_path"] or "")
        mime = str(row["mime_type"] or "")
        item_id = int(row["item_id"])
        if not force and str(row["ocr_status"] or "").lower() in {"ok", "ok_local", "ok_ai", "corrected"}:
            return {"ok": True, "status": "skipped", "chars": 0, "message": "OCR omitido: ya existe OCR válido."}
        try:
            from app.services.knowledge_ocr_service import (
                is_image_candidate,
                is_ocr_available,
                is_pdf_candidate,
                evaluate_ocr_quality,
                ocr_image_result,
                ocr_pdf,
                should_ocr_attachment,
            )

            available, reason = is_ocr_available()
            if not available:
                previous_raw = str(row["ocr_text_raw"] or row["ocr_text"] or "") if "ocr_text_raw" in row.keys() else str(row["ocr_text"] or "")
                self.update_attachment_ocr(attachment_id, previous_raw, "unavailable")
                return {"ok": False, "status": "unavailable", "chars": 0, "message": reason}
            self.update_attachment_ocr(attachment_id, str(row["ocr_text_raw"] or row["ocr_text"] or "") if "ocr_text_raw" in row.keys() else str(row["ocr_text"] or ""), "running")
            existing_text = ""
            if is_pdf_candidate(path, mime):
                existing_text = extract_text_from_attachment(path, mime, str(row["original_filename"] or ""))
            if not force and not should_ocr_attachment(path, mime, existing_text):
                self.update_attachment_ocr(attachment_id, str(row["ocr_text_raw"] or row["ocr_text"] or "") if "ocr_text_raw" in row.keys() else str(row["ocr_text"] or ""), "skipped")
                return {"ok": True, "status": "skipped", "chars": 0, "message": "OCR omitido: el adjunto no es candidato."}
            logger.info("KNOWLEDGE_OCR: rerun started attachment_id=%s", attachment_id)
            ocr_mode = ""
            ocr_rotation = None
            if is_image_candidate(path, mime):
                image_result = ocr_image_result(path)
                text = image_result.text
                ocr_mode = image_result.mode
                ocr_rotation = image_result.rotation
            else:
                text = ocr_pdf(path, max_pages=max_pdf_pages)
                ocr_mode = "PDF multipase avanzado"
            quality = evaluate_ocr_quality(text)
            status = "ok_local" if quality["quality"] == "ok" else str(quality["quality"])
            self.update_attachment_ocr(
                attachment_id,
                text,
                status,
                ocr_mode=ocr_mode,
                ocr_rotation=ocr_rotation,
                ocr_characters=len(text),
            )
            self.conn.execute(
                "UPDATE knowledge_attachments SET ocr_quality_score = ?, ocr_quality_reason = ? WHERE id = ?",
                (float(quality.get("score") or 0.0), str(quality.get("reason") or ""), attachment_id),
            )
            self.conn.commit()
            logger.info("KNOWLEDGE_OCR: local finished note_id=%s attachment_id=%s file=%s mime_type=%s extension=%s quality=%s score=%s chars=%s preview=%r", item_id, attachment_id, path, mime, Path(path).suffix.lower(), status, quality.get("score"), quality.get("chars"), text[:200])
            if status == "low_quality":
                logger.info("KNOWLEDGE_OCR: low_quality attachment_id=%s reason=%s", attachment_id, quality.get("reason"))
            if reindex and status == "ok_local":
                self.reindex_item(item_id)
            message = "OCR local correcto." if status == "ok_local" else "OCR local insuficiente; pendiente de IA." if status == "low_quality" else "OCR sin texto detectado."
            return {"ok": status == "ok_local", "status": status, "chars": len(text), "mode": ocr_mode, "rotation": ocr_rotation, "quality": quality, "message": message, "attachment_id": attachment_id}
        except Exception as exc:  # noqa: BLE001
            logger.info("KNOWLEDGE_OCR: error reason=%s", exc)
            self.update_attachment_ocr(attachment_id, str(row["ocr_text_raw"] or row["ocr_text"] or "") if "ocr_text_raw" in row.keys() else str(row["ocr_text"] or ""), "error")
            return {"ok": False, "status": "error", "chars": 0, "message": str(exc)}

    def improve_attachment_ocr_with_ai(self, attachment_id: int, *, reindex: bool = True) -> dict[str, object]:
        row = self.get_attachment(attachment_id)
        if row is None:
            return {"ok": False, "status": "error", "message": "Adjunto no encontrado.", "chars": 0}
        from app.services.knowledge_ocr_service import evaluate_ocr_quality, improve_ocr_with_ai

        current = str(row["ocr_text_raw"] or row["ocr_text"] or "")
        local_quality = evaluate_ocr_quality(current, str(row["stored_path"] or "")) if current.strip() else {"is_good_enough": False}
        if str(row["ocr_status"] or "").lower() in {"ok", "ok_local", "corrected"} and local_quality.get("is_good_enough"):
            logger.info("KNOWLEDGE_OCR_AI: skipped attachment_id=%s reason=local_ocr_sufficient score=%s", attachment_id, local_quality.get("score"))
            return {"ok": True, "status": "skipped", "message": "OCR local suficiente; no se usa IA.", "chars": len(current), "attachment_id": attachment_id, "quality": local_quality}
        filename = str(row["original_filename"] or row["stored_filename"] or "")
        logger.info("KNOWLEDGE_OCR_AI: requested attachment_id=%s filename=%s", attachment_id, filename)
        result = improve_ocr_with_ai(str(row["stored_path"] or ""), str(row["mime_type"] or ""), current)
        text = str(result.get("text") or "").strip()
        item_id = int(row["item_id"])
        if not result.get("ok") or not text:
            status = str(result.get("status") or "empty_ai")
            if status not in {"empty_ai", "error"}:
                status = "error"
            now = self._now()
            self.conn.execute(
                """
                UPDATE knowledge_attachments
                SET ocr_status = ?, ocr_updated_at = ?, updated_at = ?, ocr_engine = ?, ocr_characters = ?, ai_ocr_done = 0, ai_ocr_status = ?
                WHERE id = ?
                """,
                (status, now, now, "ai", 0, status, attachment_id),
            )
            self.conn.commit()
            if status == "error":
                logger.info("KNOWLEDGE_OCR_AI: error attachment_id=%s reason=%s", attachment_id, result.get("message") or "ai_error")
            else:
                logger.info("KNOWLEDGE_OCR_AI: empty attachment_id=%s reason=%s", attachment_id, result.get("message") or "no_useful_text")
            return {"ok": False, "status": status, "message": "No se pudo guardar el texto OCR IA en la nota" if status == "error" else "La IA no ha podido extraer texto útil.", "chars": 0, "attachment_id": attachment_id}
        now = self._now()
        cursor = self.conn.execute(
            """
            UPDATE knowledge_attachments
            SET ocr_text_ai = ?, ocr_status = ?, ocr_updated_at = ?, updated_at = ?,
                ocr_engine = ?, ocr_mode = ?, ocr_characters = ?, ocr_quality_score = ?, ocr_quality_reason = ?,
                ai_ocr_done = 1, ai_ocr_created_at = ?, ai_ocr_status = ?
            WHERE id = ?
            """,
            (text, "ok_ai", now, now, "ai", "ai", len(text), float(result.get("confidence") or 0.0), "IA visual", now, "done", attachment_id),
        )
        self.conn.commit()
        if cursor.rowcount != 1:
            logger.error("KNOWLEDGE_OCR_AI: persist failed note_id=%s attachment_id=%s rows=%s", item_id, attachment_id, cursor.rowcount)
            return {"ok": False, "status": "error_ai", "chars": len(text), "message": "No se pudo guardar el texto OCR IA en la nota", "attachment_id": attachment_id}
        logger.info("KNOWLEDGE_OCR_AI: saved OK table=knowledge_attachments field=ocr_text_ai note_id=%s attachment_id=%s rows=%s chars=%s preview=%r", item_id, attachment_id, cursor.rowcount, len(text), text[:200])
        if reindex:
            self.reindex_item(item_id)
            logger.info("KNOWLEDGE_OCR_AI: reindexed note_id=%s", item_id)
        return {"ok": True, "status": "ok_ai", "chars": len(text), "message": "Texto IA guardado correctamente en la nota", "attachment_id": attachment_id}

    def ignore_attachment_ocr(self, attachment_id: int) -> None:
        self.conn.execute("UPDATE knowledge_attachments SET ocr_status = ?, updated_at = ? WHERE id = ?", ("ignored", self._now(), attachment_id))
        self.conn.commit()

    def ocr_item_attachments(self, item_id: int) -> dict[str, int]:
        total = ok = empty = errors = 0
        for row in self.list_attachments(item_id):
            total += 1
            result = self.ocr_attachment(int(row["id"]), reindex=False)
            status = str(result.get("status") or "")
            if status in {"ok", "ok_local"}:
                ok += 1
            elif status == "low_quality":
                errors += 1
            elif status == "empty":
                empty += 1
            elif status not in {"skipped"}:
                errors += 1
        self.reindex_item(item_id)
        return {"total": total, "ok": ok, "empty": empty, "errors": errors}

    def _bulk_ocr_candidate_rows(
        self,
        *,
        include_images: bool = True,
        include_pdfs: bool = True,
        force: bool = False,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["ki.status != 'deleted'"]
        params: list[object] = []
        if not force:
            clauses.append("COALESCE(ka.ocr_status, '') NOT IN ('ok', 'ok_local', 'ok_ai', 'corrected', 'ignored')")
        extension_clauses: list[str] = []
        if include_images:
            for extension in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"):
                extension_clauses.append("LOWER(ka.original_filename) LIKE ?")
                params.append(f"%{extension}")
            extension_clauses.append("LOWER(ka.mime_type) LIKE 'image/%'")
        if include_pdfs:
            extension_clauses.append("LOWER(ka.original_filename) GLOB '*.pdf'")
            extension_clauses.append("LOWER(ka.mime_type) = 'application/pdf'")
        if not extension_clauses:
            return []
        clauses.append("(" + " OR ".join(extension_clauses) + ")")
        query = f"""
            SELECT ka.*, ki.title AS item_title
            FROM knowledge_attachments ka
            JOIN knowledge_items ki ON ki.id = ka.item_id
            WHERE {' AND '.join(clauses)}
            ORDER BY ka.item_id ASC, ka.id ASC
        """
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, int(limit)))
        return self.conn.execute(query, tuple(params)).fetchall()

    @staticmethod
    def _attachment_has_image_extension(row: sqlite3.Row) -> bool:
        return Path(str(row["original_filename"] or row["stored_filename"] or row["stored_path"] or "")).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"
        } or str(row["mime_type"] or "").lower().startswith("image/")

    @staticmethod
    def _attachment_has_pdf_extension(row: sqlite3.Row) -> bool:
        return Path(str(row["original_filename"] or row["stored_filename"] or row["stored_path"] or "")).suffix.lower() == ".pdf" or str(
            row["mime_type"] or ""
        ).lower() == "application/pdf"

    def list_bulk_ocr_candidates(
        self,
        *,
        include_images: bool = True,
        include_pdfs: bool = True,
        force: bool = False,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        rows = self._bulk_ocr_candidate_rows(
            include_images=include_images,
            include_pdfs=include_pdfs,
            force=force,
            limit=None,
        )
        candidates: list[sqlite3.Row] = []
        for row in rows:
            path = str(row["stored_path"] or "")
            mime = str(row["mime_type"] or "")
            if self._attachment_has_image_extension(row):
                candidates.append(row)
            elif self._attachment_has_pdf_extension(row):
                existing_text = extract_text_from_attachment(path, mime, str(row["original_filename"] or ""))
                if force or len(existing_text.strip()) < 80:
                    candidates.append(row)
            if limit is not None and len(candidates) >= int(limit):
                break
        return candidates

    def count_bulk_ocr_candidates(self, **options: object) -> dict[str, int]:
        candidates = self.list_bulk_ocr_candidates(
            include_images=bool(options.get("include_images", True)),
            include_pdfs=bool(options.get("include_pdfs", True)),
            force=bool(options.get("force", False)),
            limit=int(options["limit"]) if options.get("limit") is not None else None,
        )
        return {"attachments": len(candidates), "notes": len({int(row["item_id"]) for row in candidates})}

    def bulk_ocr_pending_attachments(
        self,
        *,
        include_images: bool = True,
        include_pdfs: bool = True,
        force: bool = False,
        limit: int = 100,
        max_pdf_pages: int = 5,
        cancel_event: object | None = None,
        progress_callback: object | None = None,
    ) -> dict[str, int | float | bool]:
        started = time.monotonic()
        candidates = self.list_bulk_ocr_candidates(include_images=include_images, include_pdfs=include_pdfs, force=force, limit=limit)
        total = len(candidates)
        logger.info("KNOWLEDGE_BULK_OCR: started total=%s", total)
        stats = {"candidates": total, "processed": 0, "ok_local": 0, "ok": 0, "low_quality": 0, "empty": 0, "errors": 0, "skipped": 0}
        reindex_item_ids: set[int] = set()

        def emit(event: dict[str, object]) -> None:
            if callable(progress_callback):
                progress_callback(event)

        for row in candidates:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                logger.info("KNOWLEDGE_BULK_OCR: cancelled")
                break
            attachment_id = int(row["id"])
            item_id = int(row["item_id"])
            filename = str(row["original_filename"] or row["stored_filename"] or "")
            logger.info("KNOWLEDGE_BULK_OCR: processing attachment_id=%s filename=%s", attachment_id, filename)
            emit({"type": "progress", "processed": stats["processed"], "total": total, "errors": stats["errors"], "note": row["item_title"], "attachment": filename})
            emit({"type": "log", "message": f"Procesando {filename}"})
            result = self.ocr_attachment(attachment_id, reindex=False, force=force, max_pdf_pages=max_pdf_pages)
            status = str(result.get("status") or "")
            chars = int(result.get("chars") or 0)
            stats["processed"] += 1
            if status in {"ok", "ok_local"}:
                stats["ok_local"] += 1; stats["ok"] += 1
                logger.info("KNOWLEDGE_BULK_OCR: ok_local attachment_id=%s chars=%s", attachment_id, chars)
            elif status == "low_quality":
                stats["low_quality"] += 1
                logger.info("KNOWLEDGE_BULK_OCR: low_quality attachment_id=%s", attachment_id)
            elif status == "empty":
                stats["empty"] += 1
                logger.info("KNOWLEDGE_BULK_OCR: empty attachment_id=%s", attachment_id)
            elif status == "skipped":
                stats["skipped"] += 1
            else:
                stats["errors"] += 1
                logger.info("KNOWLEDGE_BULK_OCR: error attachment_id=%s reason=%s", attachment_id, result.get("message"))
            reindex_item_ids.add(item_id)
            emit({"type": "progress", "processed": stats["processed"], "total": total, "errors": stats["errors"], "note": row["item_title"], "attachment": filename})

        for item_id in sorted(reindex_item_ids):
            self.reindex_item(item_id)
        elapsed = time.monotonic() - started
        cancelled = bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())
        logger.info("KNOWLEDGE_BULK_OCR: ok_local=%s low_quality=%s empty=%s errors=%s", stats["ok_local"], stats["low_quality"], stats["empty"], stats["errors"])
        return {**stats, "seconds": elapsed, "cancelled": cancelled}

    def list_pending_ai_ocr_attachments(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT ka.*, ki.title AS item_title
            FROM knowledge_attachments ka
            JOIN knowledge_items ki ON ki.id = ka.item_id
            WHERE ki.status != 'deleted' AND COALESCE(ka.ocr_status, '') IN ('low_quality', 'empty')
            ORDER BY ka.item_id ASC, ka.id ASC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (max(1, int(limit)),)
        return self.conn.execute(query, params).fetchall()

    def bulk_improve_pending_ocr_with_ai(self, *, limit: int = 25, cancel_event: object | None = None, progress_callback: object | None = None) -> dict[str, int | bool]:
        rows = self.list_pending_ai_ocr_attachments(limit=limit)
        logger.info("KNOWLEDGE_BULK_OCR: ai_batch started total=%s", len(rows))
        stats = {"candidates": len(rows), "processed": 0, "ok": 0, "errors": 0}
        reindex_item_ids: set[int] = set()
        for row in rows:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                break
            if callable(progress_callback):
                progress_callback({"type": "log", "message": f"IA OCR: {row['original_filename'] or row['stored_filename']}"})
            result = self.improve_attachment_ocr_with_ai(int(row["id"]), reindex=False)
            stats["processed"] += 1
            if result.get("ok"):
                stats["ok"] += 1
                reindex_item_ids.add(int(row["item_id"]))
            else:
                stats["errors"] += 1
        for item_id in sorted(reindex_item_ids):
            self.reindex_item(item_id)
        logger.info("KNOWLEDGE_BULK_OCR: ai_batch finished ok=%s errors=%s", stats["ok"], stats["errors"])
        return {**stats, "cancelled": bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())}

    def update_indexed_text(self, item_id: int, indexed_text: str) -> None:
        self.conn.execute(
            """
            UPDATE knowledge_items
            SET indexed_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (indexed_text, self._now(), item_id),
        )
        self.conn.commit()

    def _item_for_index(self, item_id: int) -> dict[str, object] | None:
        row = self.get_item(item_id)
        if row is None:
            return None
        note = dict(row)
        note["tags"] = self.get_tags_for_item(item_id)
        return note

    def reindex_item(self, item_id: int, *, apply_ocr: bool = False) -> dict[str, int | bool]:
        note = self._item_for_index(item_id)
        if note is None:
            return {"ok": False, "chars": 0}
        attachments = [dict(row) for row in self.list_attachments(item_id)]
        if apply_ocr:
            for attachment in attachments:
                self.ocr_attachment(int(attachment["id"]), reindex=False)
            attachments = [dict(row) for row in self.list_attachments(item_id)]
        payload = index_note(note, attachments)
        indexed_text = str(payload.get("indexed_text") or "")
        self.conn.execute("UPDATE knowledge_items SET indexed_text = ? WHERE id = ?", (indexed_text, item_id))
        self.conn.commit()
        chars = int(payload.get("chars") or len(indexed_text))
        logger.info("KNOWLEDGE_INDEX: note_id=%s ok chars=%s", item_id, chars)
        try:
            from app.services.knowledge_entity_service import rebuild_entities_for_note

            rebuild_entities_for_note(item_id, self.conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_ENTITY: error note_id=%s reason=%s", item_id, exc)
        return {"ok": True, "chars": chars}

    def reindex_all(self, *, apply_ocr: bool = False) -> dict[str, int | float]:
        rows = self.conn.execute(
            "SELECT id FROM knowledge_items WHERE status != 'deleted' ORDER BY id ASC"
        ).fetchall()
        total = len(rows)
        logger.info("KNOWLEDGE_INDEX: reindex started total=%s apply_ocr=%s", total, apply_ocr)
        started = time.monotonic()
        ok = 0
        errors = 0
        for row in rows:
            item_id = int(row["id"])
            try:
                result = self.reindex_item(item_id, apply_ocr=apply_ocr)
                if result.get("ok"):
                    ok += 1
                else:
                    errors += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning("KNOWLEDGE_INDEX: note_id=%s error=%s", item_id, exc)
        elapsed = time.monotonic() - started
        logger.info("KNOWLEDGE_INDEX: reindex finished ok=%s errors=%s", ok, errors)
        return {"total": total, "ok": ok, "errors": errors, "seconds": elapsed}


    def replace_entities_for_item(self, item_id: int, entities: list[dict[str, object]]) -> dict[str, int]:
        """Replace all detected entity links for a Knowledge item."""
        now = self._now()
        self.conn.execute("DELETE FROM knowledge_entity_links WHERE note_id = ?", (item_id,))
        entity_count = 0
        link_count = 0
        seen_links: set[tuple[int, str]] = set()
        for entity in entities:
            entity_type = str(entity.get("type") or entity.get("entity_type") or "other").strip().lower() or "other"
            value = str(entity.get("value") or "").strip()
            normalized_value = str(entity.get("normalized_value") or "").strip()
            source = str(entity.get("source") or "indexed_text").strip() or "indexed_text"
            snippet = str(entity.get("snippet") or "").strip()[:500]
            try:
                confidence = float(entity.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if not value or not normalized_value:
                continue
            self.conn.execute(
                """
                INSERT INTO knowledge_entities(entity_type, value, normalized_value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, normalized_value) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (entity_type, value, normalized_value, now, now),
            )
            row = self.conn.execute(
                "SELECT id FROM knowledge_entities WHERE entity_type = ? AND normalized_value = ?",
                (entity_type, normalized_value),
            ).fetchone()
            if row is None:
                continue
            entity_id = int(row["id"])
            entity_count += 1
            link_key = (entity_id, source)
            if link_key in seen_links:
                continue
            seen_links.add(link_key)
            self.conn.execute(
                """
                INSERT INTO knowledge_entity_links(entity_id, note_id, source, snippet, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, note_id, source) DO UPDATE SET
                    snippet = excluded.snippet,
                    confidence = excluded.confidence
                """,
                (entity_id, item_id, source, snippet, confidence, now),
            )
            link_count += 1
            logger.info("KNOWLEDGE_ENTITY: saved entity type=%s value=%s", entity_type, value)
        self.conn.commit()
        return {"entities": entity_count, "links": link_count}

    def rebuild_entities_for_item(self, item_id: int) -> dict[str, int | bool]:
        from app.services.knowledge_entity_service import rebuild_entities_for_note

        return rebuild_entities_for_note(item_id, self.conn)

    def rebuild_all_entities(self) -> dict[str, int | float]:
        from app.services.knowledge_entity_service import rebuild_all_entities

        return rebuild_all_entities(self.conn)

    def list_entity_types(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT entity_type, COUNT(*) AS entity_count
            FROM knowledge_entities
            GROUP BY entity_type
            ORDER BY entity_type COLLATE NOCASE ASC
            """
        ).fetchall()

    def list_entities(self, entity_type: str | None = None) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[object] = []
        if entity_type:
            clauses.append("ke.entity_type = ?")
            params.append(entity_type)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        return self.conn.execute(
            f"""
            SELECT ke.id, ke.entity_type, ke.value, ke.normalized_value,
                   COUNT(DISTINCT kel.note_id) AS note_count,
                   AVG(kel.confidence) AS avg_confidence,
                   MAX(kel.created_at) AS last_linked_at
            FROM knowledge_entities ke
            LEFT JOIN knowledge_entity_links kel ON kel.entity_id = ke.id
            {where}
            GROUP BY ke.id
            ORDER BY note_count DESC, ke.value COLLATE NOCASE ASC
            """,
            tuple(params),
        ).fetchall()

    def list_notes_for_entity(self, entity_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT ki.id, ki.title, COALESCE(NULLIF(ki.area, ''), ka.name) AS area_name,
                   kt.name AS topic_name, COALESCE(NULLIF(ki.tipo, ''), kit.name) AS item_type_name,
                   GROUP_CONCAT(DISTINCT kel.source) AS source,
                   COALESCE(
                       (SELECT kel2.snippet
                        FROM knowledge_entity_links kel2
                        WHERE kel2.entity_id = kel.entity_id AND kel2.note_id = kel.note_id
                        ORDER BY kel2.confidence DESC, kel2.id ASC
                        LIMIT 1),
                       ''
                   ) AS snippet,
                   MAX(kel.confidence) AS confidence
            FROM knowledge_entity_links kel
            JOIN knowledge_items ki ON ki.id = kel.note_id
            LEFT JOIN knowledge_areas ka ON ka.id = ki.area_id
            LEFT JOIN knowledge_topics kt ON kt.id = ki.topic_id
            LEFT JOIN knowledge_item_types kit ON kit.id = ki.item_type_id
            WHERE kel.entity_id = ? AND ki.status != 'deleted'
            GROUP BY ki.id
            ORDER BY MAX(kel.confidence) DESC, COALESCE(ki.updated_at, ki.created_at) DESC
            """,
            (entity_id,),
        ).fetchall()

    def list_entities_for_item(self, item_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT ke.id, ke.entity_type, ke.value, kel.source, kel.snippet, kel.confidence
            FROM knowledge_entity_links kel
            JOIN knowledge_entities ke ON ke.id = kel.entity_id
            WHERE kel.note_id = ?
            ORDER BY ke.entity_type COLLATE NOCASE ASC, ke.value COLLATE NOCASE ASC
            """,
            (item_id,),
        ).fetchall()

    def delete_entity(self, entity_id: int) -> None:
        self.conn.execute("DELETE FROM knowledge_entity_links WHERE entity_id = ?", (entity_id,))
        self.conn.execute("DELETE FROM knowledge_entities WHERE id = ?", (entity_id,))
        self.conn.commit()

    def merge_entities(self, target_entity_id: int, source_entity_ids: list[int]) -> None:
        source_ids = [int(entity_id) for entity_id in source_entity_ids if int(entity_id) != int(target_entity_id)]
        if not source_ids:
            return
        for source_id in source_ids:
            rows = self.conn.execute(
                "SELECT note_id, source, snippet, confidence, created_at FROM knowledge_entity_links WHERE entity_id = ?",
                (source_id,),
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    """
                    INSERT INTO knowledge_entity_links(entity_id, note_id, source, snippet, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, note_id, source) DO UPDATE SET
                        snippet = excluded.snippet,
                        confidence = MAX(knowledge_entity_links.confidence, excluded.confidence)
                    """,
                    (target_entity_id, row["note_id"], row["source"], row["snippet"], row["confidence"], row["created_at"]),
                )
            self.conn.execute("DELETE FROM knowledge_entity_links WHERE entity_id = ?", (source_id,))
            self.conn.execute("DELETE FROM knowledge_entities WHERE id = ?", (source_id,))
        self.conn.commit()

    def list_tags(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM knowledge_tags ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
        return [str(row["name"]) for row in rows]
