"""Centralized rules for ML dataset validation and deduplication."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetRule:
    required_input_text: bool = True
    required_output_text: bool = False
    required_label: bool = False
    dedupe_on: tuple[str, ...] = ("dataset", "input_text")
    min_examples_for_training: int = 10


DATASET_RULES: dict[str, DatasetRule] = {
    "email_classification": DatasetRule(
        required_input_text=True,
        required_output_text=False,
        required_label=True,
        dedupe_on=("dataset", "input_text", "label"),
        min_examples_for_training=10,
    ),
    "email_response": DatasetRule(
        required_input_text=True,
        required_output_text=True,
        required_label=False,
        dedupe_on=("dataset", "input_text", "output_text"),
    ),
    "email_summary": DatasetRule(
        required_input_text=True,
        required_output_text=True,
        required_label=False,
        dedupe_on=("dataset", "input_text", "output_text"),
    ),
    "task_detection": DatasetRule(
        required_input_text=True,
        required_output_text=False,
        required_label=True,
        dedupe_on=("dataset", "input_text", "label"),
    ),
    "calendar_event_generation": DatasetRule(
        required_input_text=True,
        required_output_text=True,
        required_label=False,
        dedupe_on=("dataset", "input_text", "output_text"),
    ),
}


def get_dataset_rule(dataset: str) -> DatasetRule:
    """Return rules for a dataset, with a safe fallback."""
    return DATASET_RULES.get((dataset or "").strip(), DatasetRule(required_label=True))
