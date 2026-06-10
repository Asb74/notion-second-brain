"""Repository for dynamic master values used by UI selects."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


class MastersRepository:
    """Data access for masters table."""

    DEFAULT_VALUES: dict[str, list[str]] = {
        "Area": ["General", "Perceco", "Informática", "Personal", "Trabajo", "Sansebas", "Archivo"],
        "Tipo": [
            "Nota",
            "Decisión",
            "Incidencia",
            "Tarea",
            "Evento",
            "Reunión",
            "Documento",
            "Procedimiento",
            "Idea",
            "Audio",
        ],
        "Estado": ["Pendiente", "En curso", "Finalizado"],
        "Prioridad": ["Baja", "Media", "Alta"],
        "Origen": ["Manual", "Sistema"],
    }

    DEFAULT_DESCRIPTIONS: dict[str, dict[str, str]] = {
        "Area": {
            "Personal": "Información personal, familiar, hogar, salud, vehículos y asuntos privados.",
            "Trabajo": "Actividad profesional diaria, producción, comercial, administración, calidad y reuniones.",
            "Sansebas": "Proyectos propios, desarrollo software, IA, automatizaciones y aplicaciones Sansebas.",
            "Archivo": "Información histórica, cerrada o de consulta, que ya no está activa pero conviene conservar.",
        },
        "Tipo": {
            "Nota": "Información general sin estructura especial.",
            "Tarea": "Acción pendiente o trabajo que requiere seguimiento.",
            "Evento": "Cita, actividad o elemento asociado a una fecha o calendario.",
            "Reunión": "Acta, conversación, acuerdos o temas tratados con otras personas.",
            "Documento": "Información procedente de un archivo, PDF, Word, Excel, imagen o documento externo.",
            "Procedimiento": "Instrucciones paso a paso para realizar una tarea o proceso.",
            "Decisión": "Acuerdo o criterio adoptado, incluyendo motivo y contexto.",
            "Incidencia": "Problema, error, reclamación o situación que requiere revisión.",
            "Idea": "Propuesta, mejora, concepto o posibilidad futura.",
            "Audio": "Nota de voz, grabación, conversación transcrita o resumen de audio.",
        },
    }

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_active(self, category: str) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT value
            FROM masters
            WHERE category = ? AND active = 1
            ORDER BY id ASC
            """,
            (category,),
        ).fetchall()
        return [str(row["value"]) for row in rows]

    def list_all(self, category: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, category, value, description, active, system_locked
            FROM masters
            WHERE category = ?
            ORDER BY active DESC, value COLLATE NOCASE ASC
            """,
            (category,),
        ).fetchall()

    def add_master(self, category: str, value: str, description: str = "") -> None:
        normalized = value.strip()
        if not normalized:
            raise ValueError("El valor del maestro no puede estar vacío")
        suggested = self.suggest_description(category, normalized)
        clean_description = description.strip() or suggested

        logger.info("MASTERS: operación local sin Notion add category=%s value=%s", category, normalized)
        self.conn.execute(
            """
            INSERT INTO masters(category, value, description, active, system_locked)
            VALUES(?, ?, ?, 1, 0)
            ON CONFLICT(category, value) DO UPDATE SET
                active = 1,
                description = CASE
                    WHEN TRIM(COALESCE(masters.description, '')) = '' THEN excluded.description
                    ELSE masters.description
                END
            """,
            (category, normalized, clean_description),
        )
        self.conn.commit()

    def update_master(self, category: str, old_value: str, new_value: str, description: str) -> None:
        normalized = new_value.strip()
        if not normalized:
            raise ValueError("El valor del maestro no puede estar vacío")
        self.conn.execute(
            """
            UPDATE masters
            SET value = ?, description = ?
            WHERE category = ? AND value = ? AND system_locked = 0
            """,
            (normalized, description.strip(), category, old_value),
        )
        self.conn.commit()
        logger.info("MASTERS: descripción actualizada category=%s value=%s", category, normalized)

    def deactivate_master(self, category: str, value: str) -> None:
        logger.info("MASTERS: operación local sin Notion deactivate category=%s value=%s", category, value)
        self.conn.execute(
            """
            UPDATE masters
            SET active = 0
            WHERE category = ? AND value = ? AND system_locked = 0
            """,
            (category, value),
        )
        self.conn.commit()

    def is_locked(self, category: str, value: str) -> bool:
        row = self.conn.execute(
            """
            SELECT system_locked
            FROM masters
            WHERE category = ? AND value = ?
            """,
            (category, value),
        ).fetchone()
        return bool(row and int(row["system_locked"]) == 1)

    @classmethod
    def suggest_description(cls, category: str, value: str) -> str:
        return cls.DEFAULT_DESCRIPTIONS.get(category, {}).get(value.strip(), "")

    # Backward compatible wrappers
    def list_values(self, field_name: str) -> list[str]:
        return self.list_active(field_name)

    def add_value(self, field_name: str, value: str) -> None:
        self.add_master(field_name, value)

    def deactivate_value(self, id: int) -> None:
        self.conn.execute("UPDATE masters SET active = 0 WHERE id = ?", (id,))
        self.conn.commit()

    def ensure_default_values(self) -> None:
        for category, values in self.DEFAULT_VALUES.items():
            for value in values:
                system_locked = 1 if category == "Estado" else 0
                description = self.suggest_description(category, value)
                self.conn.execute(
                    """
                    INSERT INTO masters(category, value, description, active, system_locked)
                    VALUES(?, ?, ?, 1, ?)
                    ON CONFLICT(category, value)
                    DO UPDATE SET
                        active = 1,
                        system_locked = MAX(system_locked, excluded.system_locked),
                        description = CASE
                            WHEN TRIM(COALESCE(masters.description, '')) = '' THEN excluded.description
                            ELSE masters.description
                        END
                    """,
                    (category, value, description, system_locked),
                )
        self.conn.commit()
