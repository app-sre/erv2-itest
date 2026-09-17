"""Shared logger setup and structured run-result recording for erv2_itest.

Logs live under .erv2-itests/logs/ and are never auto-deleted (unlike the ephemeral
Terraform working directory under .erv2-itests/runs/<run_id>/), so a scenario run can be
reviewed later regardless of --keep or pass/fail.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

LOGGER_NAME = "erv2_itest"

_console = Console()


def get_logger() -> logging.Logger:
    """The shared logger used across all erv2_itest modules."""
    return logging.getLogger(LOGGER_NAME)


def get_console() -> Console:
    """The shared Rich console for decorative, console-only output (never logged)."""
    return _console


class ConsoleHandler(logging.Handler):
    """Writes each log record as one unwrapped line to the shared Rich console.

    Deliberately not `rich.logging.RichHandler`: its `LogRender` grid `Table`
    forces `overflow="fold"` on the message column regardless of the message
    `Text`'s own wrap settings, so a single log line silently splits across
    multiple printed lines depending on console width - breaking any
    substring match (including our own tests) that spans the wrap point.
    `console.print(..., soft_wrap=True)` is the reliable way to disable that,
    and none of RichHandler's other features (timestamps, levels, tracebacks)
    are used here anyway.
    """

    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._console.print(
                self.format(record), soft_wrap=True, markup=False, highlight=False
            )
        except Exception:  # ruff: ignore[blind-except]
            # Matches logging.Handler's own documented emit() contract (see e.g.
            # the stdlib's StreamHandler.emit()): one bad log call must not crash
            # the whole run, so route it through handleError() instead.
            self.handleError(record)


def configure_logging(
    log_path: Path | None, level: int = logging.INFO
) -> logging.Logger:
    """Configure the shared logger: always to stdout, and to log_path if given."""
    logger = get_logger()
    logger.setLevel(level)
    logger.handlers.clear()

    console_handler = ConsoleHandler(get_console())
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)

    if log_path is not None:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
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
