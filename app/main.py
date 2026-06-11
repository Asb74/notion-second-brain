"""Application entrypoint."""

from __future__ import annotations

import logging
import tkinter as tk

try:
    from tkinterdnd2 import TkinterDnD
except Exception:  # noqa: BLE001
    TkinterDnD = None

from app.config.app_branding import APP_NAME
from app.core.service import NoteService
from app.persistence.db import Database, default_data_dir
from app.persistence.masters_repository import MastersRepository
from app.persistence.repositories import ActionsRepository, NoteRepository, SettingsRepository
from app.ui.app_icons import apply_app_icon
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

    service = NoteService(NoteRepository(conn), SettingsRepository(conn), masters_repo, ActionsRepository(conn))

    if TkinterDnD is not None:
        root = TkinterDnD.Tk()
        logging.getLogger(__name__).info("APP_DND: TkinterDnD disponible, root creada con TkinterDnD.Tk")
    else:
        root = tk.Tk()
        logging.getLogger(__name__).info("APP_DND: TkinterDnD no disponible, root creada con tk.Tk")
    root.title(APP_NAME)
    root.geometry("980x720")
    apply_app_icon(root)
    MainWindow(root, service, db_connection=conn)

    logging.getLogger(__name__).info("SANSEBAS_NEXUS: application started")
    logging.getLogger(__name__).info("App iniciada. Log: %s", log_path)
    root.mainloop()


if __name__ == "__main__":
    main()
