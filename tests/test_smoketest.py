"""Tests for erv2_itest's own runner mechanics.

Uses a fake, throwaway Docker image (see ../smoketest/) instead of a real ERv2
module - fast, free, no AWS needed. These exist to prove the runner correctly
detects both a passing and a failing scenario: the same kind of bug (a loop
that should signal "still in progress" via sys.exit(1) but doesn't) slipped
through manual e2e testing earlier this session and had to be caught by a
human noticing the logs - these tests catch it mechanically instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from external_resources_io.exit_status import EXIT_ERROR, EXIT_OK
from typer.testing import CliRunner

from erv2_itest import cli as cli_module

ERV2_ITEST_DIR = Path(__file__).resolve().parent.parent
SMOKETEST_DIR = ERV2_ITEST_DIR / "smoketest"
OUTPUT_DIR = ERV2_ITEST_DIR / ".erv2-itests"

runner = CliRunner()


def test_dry_run_preview_never_calls_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """With --dry-run, the scenario must resolve but Docker must never be invoked."""

    def _fail_if_called(**_: object) -> None:
        raise AssertionError("dry-run must never invoke docker")

    monkeypatch.setattr("erv2_itest.runner.run_container", _fail_if_called)

    result = runner.invoke(
        cli_module.app, [str(SMOKETEST_DIR / "scenario_pass.yaml"), "--dry-run"]
    )

    assert result.exit_code == EXIT_OK, result.output
    assert "dry-run" in result.output.lower()


@pytest.mark.usefixtures("fake_image")
def test_pass_scenario_succeeds(fixed_run_id: str) -> None:
    """A scenario whose expectations match reality passes end to end, with a persisted log+summary."""
    result = runner.invoke(
        cli_module.app,
        [str(SMOKETEST_DIR / "scenario_pass.yaml"), "--no-dry-run"],
    )

    assert result.exit_code == EXIT_OK, result.output
    assert "RESULT: PASS" in result.output

    summary = json.loads((OUTPUT_DIR / "logs" / f"{fixed_run_id}.json").read_text())
    assert summary["passed"] is True
    assert [s["name"] for s in summary["steps"]] == [
        "loop 1",
        "loop 2 settles",
        "destroy",
    ]
    assert (OUTPUT_DIR / "logs" / f"{fixed_run_id}.log").exists()
    # the ephemeral Terraform working dir is cleaned up on a fully-passed run
    assert not (OUTPUT_DIR / "runs" / fixed_run_id).exists()


@pytest.mark.usefixtures("fake_image")
def test_keep_flag_skips_cleanup_and_preserves_work_dir(fixed_run_id: str) -> None:
    """--keep must skip the Destroy cleanup step and leave the run's work dir in place."""
    result = runner.invoke(
        cli_module.app,
        [str(SMOKETEST_DIR / "scenario_pass.yaml"), "--no-dry-run", "--keep"],
    )

    assert result.exit_code == EXIT_OK, result.output
    assert "--keep given: skipping cleanup" in result.output

    summary = json.loads((OUTPUT_DIR / "logs" / f"{fixed_run_id}.json").read_text())
    assert [s["name"] for s in summary["steps"]] == ["loop 1", "loop 2 settles"]
    assert (OUTPUT_DIR / "runs" / fixed_run_id).exists()


@pytest.mark.usefixtures("fake_image")
def test_fail_scenario_reports_failure(fixed_run_id: str) -> None:
    """Regression guard: a step with a wrong expectation must fail loudly, not silently pass."""
    result = runner.invoke(
        cli_module.app,
        [str(SMOKETEST_DIR / "scenario_fail.yaml"), "--no-dry-run"],
    )

    assert result.exit_code == EXIT_ERROR, result.output
    assert "RESULT: FAIL" in result.output

    summary = json.loads((OUTPUT_DIR / "logs" / f"{fixed_run_id}.json").read_text())
    assert summary["passed"] is False
    assert summary["steps"][0]["passed"] is False
    assert "expected exit code 0, got 1" in summary["steps"][0]["reason"]
    # cleanup still ran (best-effort) even though the scenario itself failed
    assert summary["steps"][-1]["name"] == "destroy"
