"""Application entrypoint."""

from __future__ import annotations

import logging
import tkinter as tk

from app.core.service import NoteService
from app.persistence.db import Database, default_data_dir
from app.persistence.masters_repository import MastersRepository
from app.persistence.repositories import NoteRepository, SettingsRepository
from app.ui.main_window import MainWindow
from app.utils.logging_config import configure_logging


def main() -> None:
    data_dir = default_data_dir()
    log_path = configure_logging(data_dir / "logs")

    db = Database(data_dir / "notes.db")
    db.migrate()
    conn = db.connect()
    masters_repo = MastersRepository(conn)
    masters_repo.ensure_default_values()

    service = NoteService(NoteRepository(conn), SettingsRepository(conn), masters_repo)

    root = tk.Tk()
    root.title("Notion Second Brain")
    root.geometry("980x720")
    MainWindow(root, service)

    logging.getLogger(__name__).info("App iniciada. Log: %s", log_path)
    root.mainloop()


if __name__ == "__main__":
    main()
