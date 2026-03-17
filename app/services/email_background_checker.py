"""Background email checker thread primitives."""

from __future__ import annotations

from queue import Queue
from threading import Thread
import time
from typing import Callable

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
        self.running = True

    def run(self) -> None:
        while self.running:
            try:
                print("Checking emails...")
                nuevos = self.check_callback()
                if nuevos:
                    print(f"New emails detected: {len(nuevos)}")
                    self.result_queue.put(nuevos)
            except Exception as exc:  # noqa: BLE001
                print(f"Email checker error: {exc}")

            remaining = float(self.interval_seconds)
            while self.running and remaining > 0:
                step = min(0.2, remaining)
                time.sleep(step)
                remaining -= step

    def stop(self) -> None:
        self.running = False
