from app.core.knowledge_events import KnowledgeEventBus


def test_knowledge_created_notifies_subscribers_and_can_unsubscribe():
    bus = KnowledgeEventBus()
    received: list[int] = []

    unsubscribe = bus.subscribe_knowledge_created(received.append)
    bus.emit_knowledge_created(42)
    unsubscribe()
    bus.emit_knowledge_created(43)

    assert received == [42]
