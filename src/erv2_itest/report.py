"""Result reporting helpers for erv2_itest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.rule import Rule
from rich.table import Table

from .logging_utils import get_console, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger()


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


def log_scenario_divider(name: str) -> None:
    """Print a console-only divider between scenarios - purely decorative, never logged."""
    get_console().print(Rule(f"🚀 {name}", style="cyan"))


def log_step_header(name: str) -> None:
    """Log a blank line and the step's name as a small header before it runs.

    Without this, a step's `$ docker run ...` command and streamed output runs
    straight on from the previous step's with no visual separation, and the
    step's name only ever appeared once, at the very end, in the PASS/FAIL
    line - by which point its output has already scrolled past.
    """
    logger.info("")
    logger.info("  ── 👣 Step: %s ──", name)


def log_step(name: str, *, passed: bool, reason: str) -> None:
    """Log a single step's PASS/FAIL result line."""
    marker = "✅" if passed else "❌"
    suffix = f" - {reason}" if reason else ""
    logger.info("  %s %s%s", marker, name, suffix)


def log_final(
    scenario_name: str, *, passed: bool, failed_step: str, reason: str
) -> None:
    """Log the scenario's final, unambiguous result line."""
    logger.info("")
    if passed:
        logger.info("🎉 RESULT: PASS (%s)", scenario_name)
    else:
        logger.info("💥 RESULT: FAIL: %s - %s", failed_step, reason)


def _status_cell(*, passed: bool) -> str:
    return "[green]✅ PASSED[/]" if passed else "[bold red]❌ FAILED[/]"


def log_summary(results: Sequence[ScenarioResult], *, elapsed: float) -> None:
    """Print a pytest-style final report table: every scenario and step, then the totals.

    Console-only, never routed through `logging` - unlike per-step/per-scenario
    results, this aggregate view has no meaningful single "owning" log file to
    live in (it spans every scenario in the run), so there's nothing to lose by
    keeping it off the file-log path.
    """
    console = get_console()

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    parts = [f"{passed} passed"] if passed else []
    if failed:
        parts.append(f"{failed} failed")
    counts = ", ".join(parts) if parts else "0 passed"
    style = "bold red" if failed else "bold green"
    console.print(Rule(f"[{style}]{counts} in {elapsed:.2f}s[/]", style=style))

    table = Table(title="🏁 test results", title_style="bold")
    table.add_column("Scenario / Step")
    table.add_column("Status", justify="center")
    table.add_column("Reason")

    for result in results:
        table.add_row(result.name, _status_cell(passed=result.passed), "")
        for step in result.steps:
            table.add_row(
                f"  {step.name}",
                _status_cell(passed=step.passed),
                step.reason if not step.passed else "",
            )
    console.print(table)
