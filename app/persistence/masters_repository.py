"""Repository for dynamic master values used by UI selects."""

from __future__ import annotations

import sqlite3


class MastersRepository:
    """Data access for masters table."""

    DEFAULT_VALUES: dict[str, list[str]] = {
        "Area": ["General", "Perceco", "Informática"],
        "Tipo": ["Nota", "Decisión", "Incidencia"],
        "Estado": ["Pendiente", "En curso", "Finalizado"],
        "Prioridad": ["Baja", "Media", "Alta"],
    }

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_values(self, field_name: str) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT value
            FROM masters
            WHERE field_name = ? AND is_active = 1
            ORDER BY id ASC
            """,
            (field_name,),
        ).fetchall()
        return [str(row["value"]) for row in rows]

    def add_value(self, field_name: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO masters(field_name, value, is_active)
            VALUES(?, ?, 1)
            ON CONFLICT(field_name, value) DO UPDATE SET is_active = 1
            """,
            (field_name, value),
        )
        self.conn.commit()

    def deactivate_value(self, id: int) -> None:
        self.conn.execute("UPDATE masters SET is_active = 0 WHERE id = ?", (id,))
        self.conn.commit()

    def ensure_default_values(self) -> None:
        row = self.conn.execute("SELECT COUNT(1) AS count FROM masters").fetchone()
        if row and int(row["count"]) > 0:
            return

        for field_name, values in self.DEFAULT_VALUES.items():
            for value in values:
                self.conn.execute(
                    "INSERT INTO masters(field_name, value, is_active) VALUES(?, ?, 1)",
                    (field_name, value),
                )
        self.conn.commit()
