"""Unit tests for erv2_itest.report log formatting."""

from __future__ import annotations

import logging

import pytest

from erv2_itest.report import (
    SEPARATOR_WIDTH,
    ScenarioResult,
    StepResult,
    log_final,
    log_step,
    log_summary,
)


@pytest.fixture(autouse=True)  # ruff: ignore[pytest-fixture-autouse]
def _capture_erv2_itest_logger(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="erv2_itest")


def test_log_step_pass_formats_as_pass(caplog: pytest.LogCaptureFixture) -> None:
    log_step("apply", passed=True, reason="")

    assert "[PASS] apply" in caplog.text
    assert "[FAIL]" not in caplog.text


def test_log_step_fail_includes_reason(caplog: pytest.LogCaptureFixture) -> None:
    log_step("apply", passed=False, reason="expected exit code 0, got 1")

    assert "[FAIL] apply - expected exit code 0, got 1" in caplog.text


def test_log_final_pass_reports_scenario_name(caplog: pytest.LogCaptureFixture) -> None:
    log_final("my-scenario", passed=True, failed_step="", reason="")

    assert "RESULT: PASS (my-scenario)" in caplog.text
    assert "FAIL" not in caplog.text


def test_log_final_fail_reports_step_and_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log_final(
        "my-scenario",
        passed=False,
        failed_step="apply",
        reason="expected exit code 0, got 1",
    )

    assert "RESULT: FAIL: apply - expected exit code 0, got 1" in caplog.text
    assert "PASS" not in caplog.text


def test_log_summary_all_passed(caplog: pytest.LogCaptureFixture) -> None:
    results = [
        ScenarioResult(
            name="scenario-a",
            passed=True,
            steps=(StepResult(name="apply", passed=True),),
        ),
        ScenarioResult(
            name="scenario-b",
            passed=True,
            steps=(StepResult(name="apply", passed=True),),
        ),
    ]

    log_summary(results, elapsed=1.23)

    assert "test results" in caplog.text
    assert "scenario-a" in caplog.text
    assert "scenario-b" in caplog.text
    assert "2 passed in 1.23s" in caplog.text
    assert "FAILED" not in caplog.text


def test_log_summary_with_failures_lists_step_and_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    results = [
        ScenarioResult(
            name="scenario-a",
            passed=True,
            steps=(StepResult(name="apply", passed=True),),
        ),
        ScenarioResult(
            name="scenario-b",
            passed=False,
            failed_step="apply",
            reason="expected exit code 0, got 1",
            steps=(
                StepResult(
                    name="apply",
                    passed=False,
                    reason="expected exit code 0, got 1",
                ),
                StepResult(name="destroy", passed=True),
            ),
        ),
    ]

    log_summary(results, elapsed=45.32)

    assert "1 passed, 1 failed in 45.32s" in caplog.text
    assert "expected exit code 0, got 1" in caplog.text
    assert "destroy" in caplog.text


def test_log_summary_all_failed(caplog: pytest.LogCaptureFixture) -> None:
    results = [
        ScenarioResult(
            name="scenario-a", passed=False, failed_step="apply", reason="boom"
        ),
        ScenarioResult(
            name="scenario-b", passed=False, failed_step="apply", reason="boom"
        ),
    ]

    log_summary(results, elapsed=0.5)

    assert "2 failed in 0.50s" in caplog.text
    assert "passed" not in caplog.text


def test_log_summary_single_scenario_still_shown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    results = [ScenarioResult(name="my-scenario", passed=True)]

    log_summary(results, elapsed=2.1)

    assert "1 passed in 2.10s" in caplog.text


def test_log_summary_shows_steps_indented(caplog: pytest.LogCaptureFixture) -> None:
    results = [
        ScenarioResult(
            name="my-scenario",
            passed=True,
            steps=(
                StepResult(name="apply", passed=True),
                StepResult(name="destroy", passed=True),
            ),
        )
    ]

    log_summary(results, elapsed=1.0)

    assert "  apply" in caplog.text
    assert "  destroy" in caplog.text


def test_log_summary_separator_is_80_chars_wide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log_summary([ScenarioResult(name="my-scenario", passed=True)], elapsed=1.0)

    separator_lines = [
        record.message for record in caplog.records if record.message.startswith("=")
    ]
    assert separator_lines
    assert all(len(line) == SEPARATOR_WIDTH for line in separator_lines)
