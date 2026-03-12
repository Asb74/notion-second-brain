from __future__ import annotations

import json

from app.ml.ml_training_manager import MLTrainingManager


def test_save_training_example_and_deduplicate(tmp_path) -> None:
    manager = MLTrainingManager(base_dir=tmp_path)

    first = manager.save_training_example(
        dataset="email_summary",
        input_text="email content",
        output_text="summary",
        metadata={"email_id": "abc"},
    )
    second = manager.save_training_example(
        dataset="email_summary",
        input_text="email content",
        output_text="summary",
        metadata={"email_id": "abc"},
    )

    assert first["saved"] is True
    assert second == {"saved": False, "reason": "duplicate", "hash": first["hash"]}

    dataset_file = tmp_path / "email_summary.jsonl"
    lines = dataset_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["input_text"] == "email content"
    assert payload["output_text"] == "summary"
    assert payload["metadata"]["source"] == "user_confirmation"
    assert payload["metadata"]["email_id"] == "abc"


def test_trigger_retraining_when_threshold_reached(tmp_path) -> None:
    manager = MLTrainingManager(base_dir=tmp_path, retrain_threshold=2)

    first = manager.save_training_example(dataset="email_reply", input_text="i1", output_text="o1")
    second = manager.save_training_example(dataset="email_reply", input_text="i2", output_text="o2")

    assert first["retraining_triggered"] is False
    assert second["retraining_triggered"] is True

    stats = manager.dataset_stats("email_reply")
    assert stats.total_examples == 2
    assert stats.unique_examples == 2
    assert stats.pending_retraining_examples == 0


def test_register_new_dataset_supports_extensibility(tmp_path) -> None:
    manager = MLTrainingManager(base_dir=tmp_path)
    manager.register_dataset("meeting_notes")

    result = manager.save_training_example(
        dataset="meeting_notes",
        input_text="notes",
        output_text="actions",
    )

    assert result["saved"] is True
    assert (tmp_path / "meeting_notes.jsonl").exists()
