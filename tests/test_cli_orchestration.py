"""Unit tests for erv2_itest.cli's main() orchestration, without any real Docker.

Mocks run_container directly - the same boundary erv2_itest.docker_runner exposes -
so these exercise the full apply/cleanup/--keep/JSON-summary logic even in
environments with no container engine available (e.g. the Tekton test image),
complementing tests/test_smoketest.py's real-docker end-to-end coverage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from external_resources_io.exit_status import EXIT_ERROR, EXIT_OK
from typer.testing import CliRunner

from erv2_itest import cli as cli_module

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    import pytest

runner = CliRunner()

SCENARIO_YAML = """
name: unit-test-scenario
module:
  image: fake:latest
  credentials_file: credentials
base_input: base_input.json
steps:
  - name: apply
    expect:
      exit_code: 0
  - name: apply again
    expect:
      exit_code: 0
cleanup:
  name: destroy
  action: Destroy
  expect:
    exit_code: 0
"""


def _write_scenario(tmp_path: Path) -> Path:
    (tmp_path / "base_input.json").write_text(
        json.dumps({"data": {"identifier": "{{run_id}}"}}), encoding="utf-8"
    )
    (tmp_path / "credentials").write_text("fake", encoding="utf-8")
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(SCENARIO_YAML, encoding="utf-8")
    return scenario_path


def _queue_fake_run_container(
    monkeypatch: pytest.MonkeyPatch, results: Iterable[tuple[int, str]]
) -> None:
    results_iter = iter(results)

    def _fake(**_: object) -> tuple[int, str]:
        return next(results_iter)

    monkeypatch.setattr(cli_module, "run_container", _fake)


def test_all_steps_and_cleanup_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(
        monkeypatch, [(0, "ok"), (0, "ok"), (0, "Destroy complete!")]
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_OK, result.output
    assert "RESULT: PASS" in result.output

    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    [log_path] = logs_dir.glob("*.log")
    summary = json.loads(json_path.read_text(encoding="utf-8"))

    assert summary["passed"] is True
    assert [s["name"] for s in summary["steps"]] == ["apply", "apply again", "destroy"]
    assert log_path.exists()
    run_id = json_path.stem
    assert not (tmp_path / ".erv2-itests" / "runs" / run_id).exists()


def test_failing_step_stops_early_but_cleanup_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(monkeypatch, [(1, "still in progress"), (0, "destroyed")])

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert "RESULT: FAIL" in result.output

    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    summary = json.loads(json_path.read_text(encoding="utf-8"))

    assert summary["passed"] is False
    assert [s["name"] for s in summary["steps"]] == ["apply", "destroy"]
    assert summary["steps"][0]["passed"] is False
    assert summary["steps"][1]["name"] == "destroy"


def test_keep_flag_skips_cleanup_call_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(monkeypatch, [(0, "ok"), (0, "ok")])

    result = runner.invoke(
        cli_module.app, [str(scenario_path), "--no-dry-run", "--keep"]
    )

    assert result.exit_code == EXIT_OK, result.output
    assert "--keep given: skipping cleanup" in result.output

    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    summary = json.loads(json_path.read_text(encoding="utf-8"))

    assert [s["name"] for s in summary["steps"]] == ["apply", "apply again"]
    run_id = json_path.stem
    assert (tmp_path / ".erv2-itests" / "runs" / run_id).exists()
