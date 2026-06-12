"""Offline relationship service for Knowledge entities.

Relationships are calculated dynamically from the local SQLite truth source:
two entities are related when they appear in the same Knowledge note.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

TITLE_SOURCE = "title"
CONTENT_SOURCES = {"content", "indexed_text", "summary", "tags"}


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


class KnowledgeRelationService:
    """Build lightweight Knowledge graph views from extracted entity links."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_entity(self, entity_id: int) -> dict[str, Any]:
        """Return one entity enriched with basic occurrence statistics."""
        row = self.conn.execute(
            """
            SELECT ke.id, ke.entity_type, ke.value, ke.normalized_value,
                   COUNT(DISTINCT CASE WHEN ki.status != 'deleted' THEN kel.note_id END) AS note_count,
                   AVG(CASE WHEN ki.status != 'deleted' THEN kel.confidence END) AS avg_confidence,
                   MIN(COALESCE(ki.created_at, kel.created_at)) AS first_seen_at,
                   MAX(COALESCE(ki.updated_at, ki.created_at, kel.created_at)) AS last_seen_at,
                   MIN(ke.created_at) AS created_at,
                   MAX(COALESCE(ke.updated_at, ke.created_at)) AS updated_at
            FROM knowledge_entities ke
            LEFT JOIN knowledge_entity_links kel ON kel.entity_id = ke.id
            LEFT JOIN knowledge_items ki ON ki.id = kel.note_id
            WHERE ke.id = ?
            GROUP BY ke.id
            """,
            (int(entity_id),),
        ).fetchone()
        return _row_to_dict(row)

    def get_notes_by_entity(self, entity_id: int) -> list[dict[str, Any]]:
        """Return active notes where an entity appears, with navigation metadata."""
        rows = self.conn.execute(
            """
            SELECT ki.id, ki.title, COALESCE(NULLIF(ki.area, ''), ka.name) AS area_name,
                   kt.name AS topic_name, COALESCE(NULLIF(ki.tipo, ''), kit.name) AS item_type_name,
                   ki.source_type, ki.source_id,
                   COALESCE(ki.updated_at, ki.created_at) AS note_date,
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
            ORDER BY COALESCE(ki.updated_at, ki.created_at) DESC, ki.id DESC
            """,
            (int(entity_id),),
        ).fetchall()
        notes = [_row_to_dict(row) for row in rows]
        logger.info("KNOWLEDGE_RELATION: notes entity_id=%s count=%s", entity_id, len(notes))
        return notes

    def get_related_entities(self, entity_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Return entities co-occurring in notes with the selected entity."""
        base_rows = self.conn.execute(
            """
            SELECT kel.note_id, kel.source
            FROM knowledge_entity_links kel
            JOIN knowledge_items ki ON ki.id = kel.note_id
            WHERE kel.entity_id = ? AND ki.status != 'deleted'
            """,
            (int(entity_id),),
        ).fetchall()
        base_sources_by_note: dict[int, set[str]] = {}
        for row in base_rows:
            base_sources_by_note.setdefault(int(row["note_id"]), set()).add(str(row["source"] or ""))
        if not base_sources_by_note:
            logger.info("KNOWLEDGE_RELATION: related entity_id=%s count=0", entity_id)
            return []

        note_ids = sorted(base_sources_by_note)
        placeholders = ",".join("?" for _ in note_ids)
        rows = self.conn.execute(
            f"""
            SELECT ke.id AS entity_id, ke.value, ke.entity_type AS type,
                   kel.note_id, kel.source, kel.snippet, kel.confidence,
                   ki.title, COALESCE(NULLIF(ki.area, ''), ka.name) AS area_name,
                   kt.name AS topic_name, COALESCE(ki.updated_at, ki.created_at) AS note_date
            FROM knowledge_entity_links kel
            JOIN knowledge_entities ke ON ke.id = kel.entity_id
            JOIN knowledge_items ki ON ki.id = kel.note_id
            LEFT JOIN knowledge_areas ka ON ka.id = ki.area_id
            LEFT JOIN knowledge_topics kt ON kt.id = ki.topic_id
            WHERE kel.note_id IN ({placeholders})
              AND kel.entity_id != ?
              AND ki.status != 'deleted'
            ORDER BY ke.value COLLATE NOCASE ASC, kel.note_id ASC
            """,
            (*note_ids, int(entity_id)),
        ).fetchall()

        relation_sources: dict[int, dict[int, set[str]]] = {}
        relation_examples: dict[int, dict[int, dict[str, Any]]] = {}
        relation_meta: dict[int, dict[str, Any]] = {}
        for row in rows:
            related_id = int(row["entity_id"])
            note_id = int(row["note_id"])
            source = str(row["source"] or "")
            relation_meta.setdefault(
                related_id,
                {
                    "entity_id": related_id,
                    "value": row["value"] or "",
                    "type": row["type"] or "other",
                },
            )
            relation_sources.setdefault(related_id, {}).setdefault(note_id, set()).add(source)
            relation_examples.setdefault(related_id, {}).setdefault(
                note_id,
                {
                    "note_id": note_id,
                    "title": row["title"] or "",
                    "area_name": row["area_name"] or "",
                    "topic_name": row["topic_name"] or "",
                    "note_date": row["note_date"] or "",
                    "snippet": row["snippet"] or "",
                    "confidence": float(row["confidence"] or 0.0),
                },
            )

        relations: list[dict[str, Any]] = []
        for related_id, notes_sources in relation_sources.items():
            score = 0.0
            for note_id, related_sources in notes_sources.items():
                base_sources = base_sources_by_note.get(note_id, set())
                score += 1.0
                if base_sources & related_sources:
                    score += 0.5
                base_in_title = TITLE_SOURCE in base_sources
                related_in_title = TITLE_SOURCE in related_sources
                base_in_content = bool(base_sources & CONTENT_SOURCES)
                related_in_content = bool(related_sources & CONTENT_SOURCES)
                if (base_in_title and related_in_content) or (related_in_title and base_in_content):
                    score += 1.0
            examples = sorted(
                relation_examples.get(related_id, {}).values(),
                key=lambda item: str(item.get("note_date") or ""),
                reverse=True,
            )[:3]
            relations.append(
                {
                    **relation_meta[related_id],
                    "shared_notes_count": len(notes_sources),
                    "score": round(score, 2),
                    "examples": examples,
                }
            )

        relations.sort(key=lambda item: (-float(item["score"]), -int(item["shared_notes_count"]), str(item["value"]).casefold()))
        limited = relations[: max(1, int(limit or 50))]
        logger.info("KNOWLEDGE_RELATION: related entity_id=%s count=%s", entity_id, len(limited))
        return limited

    def get_entity_timeline(self, entity_id: int) -> list[dict[str, Any]]:
        """Return notes for an entity ordered chronologically descending."""
        timeline = self.get_notes_by_entity(entity_id)
        timeline.sort(key=lambda item: str(item.get("note_date") or ""), reverse=True)
        return timeline

    def get_entity_profile(self, entity_id: int) -> dict[str, Any]:
        """Return entity, note, relation and stat blocks for the entity profile."""
        entity = self.get_entity(entity_id)
        notes = self.get_notes_by_entity(entity_id)
        related_entities = self.get_related_entities(entity_id)
        stats = {
            "notes_count": len(notes),
            "related_entities_count": len(related_entities),
            "avg_confidence": float(entity.get("avg_confidence") or 0.0) if entity else 0.0,
            "first_seen_at": entity.get("first_seen_at") if entity else "",
            "last_seen_at": entity.get("last_seen_at") if entity else "",
        }
        logger.info("KNOWLEDGE_RELATION: profile entity_id=%s", entity_id)
        return {"entity": entity, "notes": notes, "related_entities": related_entities, "stats": stats}


_DEFAULT_SERVICE: KnowledgeRelationService | None = None


def _service(conn: sqlite3.Connection | None = None) -> KnowledgeRelationService:
    global _DEFAULT_SERVICE
    if conn is None:
        if _DEFAULT_SERVICE is None:
            raise ValueError("Se requiere una conexión SQLite para KnowledgeRelationService")
        return _DEFAULT_SERVICE
    _DEFAULT_SERVICE = KnowledgeRelationService(conn)
    return _DEFAULT_SERVICE


def get_entity_profile(entity_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    return _service(conn).get_entity_profile(entity_id)


def get_related_entities(entity_id: int, limit: int = 50, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    return _service(conn).get_related_entities(entity_id, limit=limit)


def get_notes_by_entity(entity_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    return _service(conn).get_notes_by_entity(entity_id)


def get_entity_timeline(entity_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    return _service(conn).get_entity_timeline(entity_id)
