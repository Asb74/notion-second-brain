"""Logging setup for file and console outputs."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_dir: Path) -> Path:
    """Configure logging and return log file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return log_file
