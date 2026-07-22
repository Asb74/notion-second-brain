"""Notifications shared by modules that create Knowledge entries.

The bus deliberately carries only a small, stable payload so capture sources do
not need to know about any Knowledge Manager UI implementation details.
"""

from __future__ import annotations

from collections.abc import Callable


KnowledgeCreatedCallback = Callable[[int], None]


class KnowledgeEventBus:
    """In-process observer for Knowledge lifecycle events."""

    def __init__(self) -> None:
        self._knowledge_created_callbacks: list[KnowledgeCreatedCallback] = []

    def subscribe_knowledge_created(self, callback: KnowledgeCreatedCallback) -> Callable[[], None]:
        """Register *callback* and return a function that removes it."""
        self._knowledge_created_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._knowledge_created_callbacks:
                self._knowledge_created_callbacks.remove(callback)

        return unsubscribe

    def emit_knowledge_created(self, note_id: int) -> None:
        """Notify listeners that a new knowledge item was persisted."""
        for callback in tuple(self._knowledge_created_callbacks):
            callback(int(note_id))


knowledge_event_bus = KnowledgeEventBus()
