"""Repository for dynamic master values used by UI selects."""

from __future__ import annotations

import sqlite3


class MastersRepository:
    """Data access for masters table."""

    DEFAULT_VALUES: dict[str, list[str]] = {
        "Area": ["General", "Perceco", "Informática"],
        "Tipo": ["Nota", "Decisión", "Incidencia", "Tarea"],
        "Estado": ["Pendiente", "En curso", "Finalizado"],
        "Prioridad": ["Baja", "Media", "Alta"],
        "Origen": ["Manual", "Sistema"],
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
            SELECT id, category, value, active, system_locked
            FROM masters
            WHERE category = ?
            ORDER BY active DESC, value COLLATE NOCASE ASC
            """,
            (category,),
        ).fetchall()

    def add_master(self, category: str, value: str) -> None:
        normalized = value.strip()
        if not normalized:
            raise ValueError("El valor del maestro no puede estar vacío")

        self.conn.execute(
            """
            INSERT INTO masters(category, value, active, system_locked)
            VALUES(?, ?, 1, 0)
            ON CONFLICT(category, value) DO UPDATE SET active = 1
            """,
            (category, normalized),
        )
        self.conn.commit()

    def deactivate_master(self, category: str, value: str) -> None:
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
                self.conn.execute(
                    """
                    INSERT INTO masters(category, value, active, system_locked)
                    VALUES(?, ?, 1, ?)
                    ON CONFLICT(category, value)
                    DO UPDATE SET active = 1, system_locked = MAX(system_locked, excluded.system_locked)
                    """,
                    (category, value, system_locked),
                )
        self.conn.commit()
