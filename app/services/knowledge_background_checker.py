"""Periodic, non-blocking runner for the existing mobile knowledge import flow."""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class KnowledgeBackgroundChecker:
    """Run one shared capture callback at a time without touching Tk widgets."""

    def __init__(self, download_callback: Callable[[], int], result_callback: Callable[[int, Exception | None], None], interval_minutes: int = 10) -> None:
        self.download_callback = download_callback
        self.result_callback = result_callback
        self.interval_seconds = max(60, int(interval_minutes) * 60)
        self._stop = threading.Event()
        self._running = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, run_immediately: bool = False) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(run_immediately,), daemon=True, name="knowledge-background")
        self._thread.start()
        logger.info("KNOWLEDGE_BACKGROUND: iniciado intervalo_minutos=%s", self.interval_seconds // 60)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("KNOWLEDGE_BACKGROUND: detenido")

    def trigger_now(self) -> bool:
        if not self._running.acquire(blocking=False):
            logger.info("KNOWLEDGE_BACKGROUND: ejecución omitida, ya hay otra activa")
            return False
        threading.Thread(target=self._execute, daemon=True, name="knowledge-download-now").start()
        return True

    def _loop(self, run_immediately: bool) -> None:
        if run_immediately:
            self.trigger_now()
        while not self._stop.wait(self.interval_seconds):
            self.trigger_now()

    def _execute(self) -> None:
        logger.info("KNOWLEDGE_BACKGROUND: ejecución iniciada")
        try:
            created = int(self.download_callback() or 0)
            logger.info("KNOWLEDGE_BACKGROUND: %s", "sin novedades" if not created else f"nuevas entradas={created}")
            self.result_callback(created, None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("KNOWLEDGE_BACKGROUND: error de ejecución")
            self.result_callback(0, exc)
        finally:
            self._running.release()
