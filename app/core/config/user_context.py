"""User context helpers backed by database state."""

from __future__ import annotations

import sqlite3


def get_user_email(conn: sqlite3.Connection) -> str:
    """Return current user email from user_profile table."""
    try:
        cursor = conn.execute("SELECT email FROM user_profile LIMIT 1")
        row = cursor.fetchone()
        return str(row[0]).strip().lower() if row and row[0] else ""
    except Exception:  # noqa: BLE001
        return ""
