"""SQLite repository for the Knowledge Manager module."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


class KnowledgeRepository:
    """Data access layer for generic knowledge items, areas, types, and tags."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

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
                summary,
                area_id,
                cleaned_area,
                topic_id,
                item_type_id,
                cleaned_tipo,
                source_type.strip() or "manual",
                source_id.strip(),
                source_path.strip(),
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
        self.set_tags_for_item(item_id, tags or [])
        self.conn.commit()
        return item_id

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
            like = f"%{cleaned_search}%"
            clauses.append(
                "(ki.title LIKE ? OR ki.content LIKE ? OR ki.summary LIKE ? OR "
                "EXISTS (SELECT 1 FROM knowledge_item_tags kit2 "
                "JOIN knowledge_tags kt ON kt.id = kit2.tag_id "
                "WHERE kit2.item_id = ki.id AND kt.name LIKE ?))"
            )
            params.extend([like, like, like, like])
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
                   COALESCE(NULLIF(ki.tipo, ''), kit.name) AS item_type_name
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

    def delete_item(self, item_id: int) -> None:
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
        return int(cursor.lastrowid)

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
        self.conn.execute("DELETE FROM knowledge_attachments WHERE id = ?", (attachment_id,))
        self.conn.commit()

    def list_tags(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM knowledge_tags ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
        return [str(row["name"]) for row in rows]
