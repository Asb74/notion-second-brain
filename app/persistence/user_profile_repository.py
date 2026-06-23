"""Repository for the singleton user profile."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from app.config.config_paths import legacy_app_data_dir

logger = logging.getLogger(__name__)


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

    def get_email(self) -> str:
        """Return the configured profile email, or an empty string."""
        profile = self.get_profile()
        return str(profile.get("email", "")).strip().lower()

    def save_email(self, email: str) -> None:
        """Update only the profile email, preserving the rest of the profile."""
        profile = self.get_profile()
        self.save_profile(
            nombre=profile.get("nombre", ""),
            cargo=profile.get("cargo", ""),
            empresa=profile.get("empresa", ""),
            telefono=profile.get("telefono", ""),
            email=email.strip().lower(),
            dominio_interno=profile.get("dominio_interno", ""),
        )

    def migrate_managed_email_from_legacy(self, legacy_db_path: Path | None = None) -> str:
        """Copy the managed email from the legacy NotionSecondBrain DB when current profile is empty."""
        current_email = self.get_email()
        if current_email:
            return current_email

        source = legacy_db_path or (legacy_app_data_dir() / "notes.db")
        if not source.exists():
            return ""

        legacy_email = self._read_legacy_email(source)
        if not legacy_email:
            return ""

        self.save_email(legacy_email)
        logger.info("USER_PROFILE: migrated managed email from legacy DB %s", source)
        return legacy_email

    @staticmethod
    def _read_legacy_email(db_path: Path) -> str:
        try:
            with sqlite3.connect(str(db_path)) as legacy_conn:
                for query in (
                    "SELECT email FROM user_profile WHERE id = 1",
                    "SELECT value FROM settings WHERE key = 'managed_email'",
                ):
                    try:
                        row = legacy_conn.execute(query).fetchone()
                    except sqlite3.Error:
                        continue
                    if row and row[0]:
                        return str(row[0]).strip().lower()
        except sqlite3.Error:
            logger.exception("USER_PROFILE: could not read legacy managed email from %s", db_path)
        return ""

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
