from queue import Empty, Queue

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


def test_email_checker_thread_logs_error_without_enqueuing_structured_error() -> None:
    output: Queue[object] = Queue()

    def callback() -> list[dict[str, str]]:
        raise RuntimeError("boom")

    thread = EmailCheckerThread(check_callback=callback, result_queue=output, interval_seconds=10)
    thread.start()

    thread.stop()
    thread.join(timeout=1)

    try:
        output.get_nowait()
        assert False, "No deberían encolarse errores estructurados"
    except Empty:
        assert True
