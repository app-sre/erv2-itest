"""Unit tests for erv2_itest.logging_utils."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from erv2_itest.logging_utils import (
    RunRecord,
    StepRecord,
    configure_logging,
    get_logger,
    now_iso,
)

if TYPE_CHECKING:
    from pathlib import Path

ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
STDOUT_AND_FILE_HANDLER_COUNT = 2


def test_get_logger_returns_same_instance() -> None:
    assert get_logger() is get_logger()


def test_configure_logging_without_path_adds_only_stdout_handler() -> None:
    logger = configure_logging(log_path=None)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert not isinstance(logger.handlers[0], logging.FileHandler)


def test_configure_logging_with_path_adds_stdout_and_file_handlers(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.log"

    logger = configure_logging(log_path=log_path)

    assert len(logger.handlers) == STDOUT_AND_FILE_HANDLER_COUNT
    handler_types = {type(h) for h in logger.handlers}
    assert logging.FileHandler in handler_types
    assert logging.StreamHandler in handler_types


def test_configure_logging_actually_writes_to_the_file(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    logger = configure_logging(log_path=log_path)

    logger.info("hello from the test suite")
    for handler in logger.handlers:
        handler.flush()

    assert "hello from the test suite" in log_path.read_text(encoding="utf-8")


def test_configure_logging_replaces_previous_handlers(tmp_path: Path) -> None:
    configure_logging(log_path=tmp_path / "first.log")
    logger = configure_logging(log_path=None)

    assert len(logger.handlers) == 1


def test_now_iso_is_a_valid_utc_iso_8601_timestamp() -> None:
    before = datetime.now(UTC)
    timestamp = now_iso()
    after = datetime.now(UTC)

    assert ISO_8601_RE.match(timestamp)
    parsed = datetime.fromisoformat(timestamp)
    assert before <= parsed <= after


def test_step_record_defaults() -> None:
    step = StepRecord(name="apply", action="Apply", dry_run=False, expected_exit_code=0)

    assert step.actual_exit_code is None
    assert step.passed is False
    assert not step.reason


def test_run_record_defaults() -> None:
    record = RunRecord(
        scenario_name="my-scenario",
        run_id="erv2it-test",
        image="my-image:latest",
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert not record.finished_at
    assert record.passed is False
    assert record.steps == []


def test_run_record_to_json_round_trips() -> None:
    record = RunRecord(
        scenario_name="my-scenario",
        run_id="erv2it-test",
        image="my-image:latest",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:05:00+00:00",
        passed=True,
    )
    record.steps.append(
        StepRecord(
            name="apply",
            action="Apply",
            dry_run=False,
            expected_exit_code=0,
            actual_exit_code=0,
            passed=True,
            reason="",
        )
    )

    parsed = json.loads(record.to_json())

    assert parsed == {
        "scenario_name": "my-scenario",
        "run_id": "erv2it-test",
        "image": "my-image:latest",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:05:00+00:00",
        "passed": True,
        "steps": [
            {
                "name": "apply",
                "action": "Apply",
                "dry_run": False,
                "expected_exit_code": 0,
                "actual_exit_code": 0,
                "passed": True,
                "reason": "",
            }
        ],
    }
