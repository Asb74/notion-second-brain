"""Repository for the singleton user profile."""

from __future__ import annotations

import sqlite3


class UserProfileRepository:
    """Read and write the single user profile row."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_profile(self) -> dict[str, str]:
        row = self.conn.execute(
            """
            SELECT id, nombre, cargo, empresa, telefono, email, dominio_interno
            FROM user_profile
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO user_profile(id) VALUES(1)")
            self.conn.commit()
            return {
                "id": "1",
                "nombre": "",
                "cargo": "",
                "empresa": "",
                "telefono": "",
                "email": "",
                "dominio_interno": "",
            }
        return {key: str(row[key] or "") for key in row.keys()}

    def save_profile(
        self,
        nombre: str,
        cargo: str,
        empresa: str,
        telefono: str,
        email: str,
        dominio_interno: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO user_profile (id, nombre, cargo, empresa, telefono, email, dominio_interno)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre=excluded.nombre,
                cargo=excluded.cargo,
                empresa=excluded.empresa,
                telefono=excluded.telefono,
                email=excluded.email,
                dominio_interno=excluded.dominio_interno
            """,
            (nombre.strip(), cargo.strip(), empresa.strip(), telefono.strip(), email.strip(), dominio_interno.strip()),
        )
        self.conn.commit()
