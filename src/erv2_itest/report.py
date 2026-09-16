"""Result reporting helpers for erv2_itest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .logging_utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger()


def log_step(name: str, *, passed: bool, reason: str) -> None:
    """Log a single step's PASS/FAIL result line."""
    status = "PASS" if passed else "FAIL"
    suffix = f" - {reason}" if reason else ""
    logger.info("  [%s] %s%s", status, name, suffix)


def log_final(
    scenario_name: str, *, passed: bool, failed_step: str, reason: str
) -> None:
    """Log the scenario's final, unambiguous result line."""
    logger.info("")
    if passed:
        logger.info("RESULT: PASS (%s)", scenario_name)
    else:
        logger.info("RESULT: FAIL: %s - %s", failed_step, reason)


def log_summary(results: Sequence[tuple[str, bool]]) -> None:
    """Log a pytest-style aggregate line across multiple scenario runs."""
    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed
    logger.info("")
    logger.info("SUMMARY: %s passed, %s failed (of %s)", passed, failed, len(results))
