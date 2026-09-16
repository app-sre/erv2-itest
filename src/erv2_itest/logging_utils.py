"""Shared logger setup and structured run-result recording for erv2_itest.

Logs live under .erv2-itests/logs/ and are never auto-deleted (unlike the ephemeral
Terraform working directory under .erv2-itests/runs/<run_id>/), so a scenario run can be
reviewed later regardless of --keep or pass/fail.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

LOGGER_NAME = "erv2_itest"


def get_logger() -> logging.Logger:
    """The shared logger used across all erv2_itest modules."""
    return logging.getLogger(LOGGER_NAME)


def configure_logging(log_path: Path | None) -> logging.Logger:
    """Configure the shared logger: always to stdout, and to log_path if given."""
    logger = get_logger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_path is not None:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


@dataclass
class StepRecord:
    """A single step's recorded outcome, for the structured JSON summary."""

    name: str
    action: str
    dry_run: bool
    expected_exit_code: int
    actual_exit_code: int | None = None
    passed: bool = False
    reason: str = ""


@dataclass
class RunRecord:
    """The full structured summary of one scenario run, written as JSON."""

    scenario_name: str
    run_id: str
    image: str
    started_at: str
    finished_at: str = ""
    passed: bool = False
    steps: list[StepRecord] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize this record to a pretty-printed JSON string."""
        return json.dumps(
            {
                "scenario_name": self.scenario_name,
                "run_id": self.run_id,
                "image": self.image,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "passed": self.passed,
                "steps": [vars(s) for s in self.steps],
            },
            indent=2,
        )
