"""Result reporting helpers for erv2_itest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .logging_utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger()

SEPARATOR_WIDTH = 80


@dataclass(frozen=True)
class StepResult:
    """One step's PASS/FAIL outcome, for the final pytest-style report."""

    name: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario's outcome (with its steps), for the final pytest-style report."""

    name: str
    passed: bool
    failed_step: str = ""
    reason: str = ""
    steps: tuple[StepResult, ...] = ()


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


def _separator(text: str, *, width: int = SEPARATOR_WIDTH) -> str:
    """Center text in a pytest-style `=`-padded line, e.g. `==== text ====`."""
    padded = f" {text} "
    fill = max(width - len(padded), 0)
    left = fill // 2
    right = fill - left
    return f"{'=' * left}{padded}{'=' * right}"


def _result_line(label: str, *, passed: bool, width: int = SEPARATOR_WIDTH) -> str:
    """`label`, right-padded with spaces so the PASSED/FAILED marker aligns at `width`."""
    marker = "✅ PASSED" if passed else "❌ FAILED"
    padding = max(width - len(label) - len(marker) - 1, 1)
    return f"{label}{' ' * padding}{marker}"


def log_summary(results: Sequence[ScenarioResult], *, elapsed: float) -> None:
    """Log a pytest-style final report: every scenario and step, then the totals."""
    logger.info("")
    logger.info(_separator("test results"))
    for result in results:
        logger.info(_result_line(result.name, passed=result.passed))
        for step in result.steps:
            logger.info(_result_line(f"  {step.name}", passed=step.passed))
            if not step.passed and step.reason:
                logger.info("    %s", step.reason)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    parts = [f"{passed} passed"] if passed else []
    if failed:
        parts.append(f"{failed} failed")
    counts = ", ".join(parts) if parts else "0 passed"
    logger.info(_separator(f"{counts} in {elapsed:.2f}s"))
