"""Unit tests for erv2_itest.report log formatting."""

from __future__ import annotations

import logging

import pytest

from erv2_itest.report import log_final, log_step


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
