from queue import Queue

from app.services.email_background_checker import EmailCheckerThread


def test_email_checker_thread_enqueues_results_and_stops() -> None:
    output: Queue[object] = Queue()

    def callback() -> list[dict[str, str]]:
        return [{"gmail_id": "1"}]

    thread = EmailCheckerThread(check_callback=callback, result_queue=output, interval_seconds=10)
    thread.start()

    item = output.get(timeout=1)
    assert isinstance(item, list)
    assert item[0]["gmail_id"] == "1"

    thread.stop()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_email_checker_thread_enqueues_structured_error() -> None:
    output: Queue[object] = Queue()

    def callback() -> list[dict[str, str]]:
        raise RuntimeError("boom")

    thread = EmailCheckerThread(check_callback=callback, result_queue=output, interval_seconds=10)
    thread.start()

    item = output.get(timeout=1)
    assert isinstance(item, dict)
    assert item["type"] == "error"
    assert "boom" in item["error"]

    thread.stop()
    thread.join(timeout=1)
