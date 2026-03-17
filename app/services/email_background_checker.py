"""Background email checker thread primitives."""

from __future__ import annotations

import logging
from queue import Queue
from threading import Event, Thread
from typing import Callable

logger = logging.getLogger(__name__)

EmailCheckPayload = list[dict[str, str]] | dict[str, str]


class EmailCheckerThread(Thread):
    """Run email checks periodically and push results into a thread-safe queue."""

    def __init__(
        self,
        check_callback: Callable[[], list[dict[str, str]]],
        result_queue: Queue[EmailCheckPayload],
        interval_seconds: int = 60,
    ):
        super().__init__(daemon=True)
        self.check_callback = check_callback
        self.result_queue = result_queue
        self.interval_seconds = max(10, int(interval_seconds))
        self._stop_event = Event()

    def run(self) -> None:
        logger.info("Email checker thread started")
        while not self._stop_event.is_set():
            try:
                logger.debug("Checking new emails in background")
                nuevos = self.check_callback()
                if nuevos:
                    self.result_queue.put(nuevos)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Background email checker failed")
                self.result_queue.put({"type": "error", "error": str(exc)})

            if self._stop_event.wait(self.interval_seconds):
                break

        logger.info("Email checker thread stopped")

    def stop(self) -> None:
        self._stop_event.set()
