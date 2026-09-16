"""Unit tests for erv2_itest.cli's main() orchestration, without any real Docker.

Mocks run_container directly - the same boundary erv2_itest.runner.DockerErv2Runner
uses - so these exercise the full apply/cleanup/--keep/JSON-summary/multi-scenario
logic even in environments with no container engine available (e.g. the Tekton test
image), complementing tests/test_smoketest.py's real-docker end-to-end coverage.
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

TWO_SCENARIOS = 2

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


def _write_scenario(
    cwd_root: Path,
    *,
    yaml_dir: Path | None = None,
    filename: str = "scenario.yaml",
    body: str = SCENARIO_YAML,
) -> Path:
    """Write base_input.json/credentials at cwd_root and the scenario YAML at yaml_dir.

    CWD-relative paths in the scenario resolve against cwd_root; yaml_dir (defaults
    to cwd_root) is only where the scenario file itself lives.
    """
    if not (cwd_root / "base_input.json").exists():
        (cwd_root / "base_input.json").write_text(
            json.dumps({"data": {"identifier": "{{run_id}}"}}), encoding="utf-8"
        )
    if not (cwd_root / "credentials").exists():
        (cwd_root / "credentials").write_text("fake", encoding="utf-8")
    scenario_path = (yaml_dir or cwd_root) / filename
    scenario_path.write_text(body, encoding="utf-8")
    return scenario_path


def _queue_fake_run_container(
    monkeypatch: pytest.MonkeyPatch, results: Iterable[tuple[int, str]]
) -> None:
    results_iter = iter(results)

    def _fake(**_: object) -> tuple[int, str]:
        return next(results_iter)

    monkeypatch.setattr("erv2_itest.runner.run_container", _fake)


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


def test_cleanup_failure_fails_the_scenario_even_if_all_steps_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(
        monkeypatch, [(0, "ok"), (0, "ok"), (1, "destroy failed")]
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert "RESULT: FAIL: destroy" in result.output

    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    summary = json.loads(json_path.read_text(encoding="utf-8"))

    assert summary["passed"] is False
    assert summary["steps"][-1]["name"] == "destroy"
    assert summary["steps"][-1]["passed"] is False


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


def test_no_args_auto_discovers_scenarios_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="a.yaml")
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="b.yaml")
    _queue_fake_run_container(monkeypatch, [(0, "ok"), (0, "ok"), (0, "ok")] * 2)

    result = runner.invoke(cli_module.app, ["--no-dry-run"])

    assert result.exit_code == EXIT_OK, result.output
    assert "SUMMARY: 2 passed, 0 failed (of 2)" in result.output


def test_discover_scenarios_recurses_into_subdirectories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scenarios can be organized into subdirectories under scenarios_dir."""
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    auth_dir = scenarios_dir / "auth"
    auth_dir.mkdir(parents=True)
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="top-level.yaml")
    _write_scenario(tmp_path, yaml_dir=auth_dir, filename="nested.yaml")
    _queue_fake_run_container(monkeypatch, [(0, "ok"), (0, "ok"), (0, "ok")] * 2)

    result = runner.invoke(cli_module.app, [])

    assert result.exit_code == EXIT_OK, result.output
    assert "SUMMARY: 2 passed, 0 failed (of 2)" in result.output


def test_select_matches_a_subdirectory_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    auth_dir = scenarios_dir / "auth"
    auth_dir.mkdir(parents=True)
    (scenarios_dir / "other").mkdir()
    _write_scenario(tmp_path, yaml_dir=auth_dir, filename="scenario.yaml")
    _write_scenario(
        tmp_path, yaml_dir=scenarios_dir / "other", filename="scenario.yaml"
    )
    _queue_fake_run_container(monkeypatch, [(0, "ok"), (0, "ok"), (0, "ok")])

    result = runner.invoke(cli_module.app, ["-k", "auth"])

    assert result.exit_code == EXIT_OK, result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    assert len(list(logs_dir.glob("*.json"))) == 1


def test_select_filters_auto_discovered_scenarios(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="wanted.yaml")
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="skip-me.yaml")
    _queue_fake_run_container(monkeypatch, [(0, "ok"), (0, "ok"), (0, "ok")])

    result = runner.invoke(cli_module.app, ["--no-dry-run", "-k", "wanted"])

    assert result.exit_code == EXIT_OK, result.output
    assert "unit-test-scenario" in result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    assert len(list(logs_dir.glob("*.json"))) == 1


def test_fail_fast_stops_after_first_failing_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="a.yaml")
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="b.yaml")
    # a.yaml's two steps both fail immediately; b.yaml would pass if it ran.
    _queue_fake_run_container(
        monkeypatch, [(1, "boom"), (0, "destroyed"), (0, "ok"), (0, "ok"), (0, "ok")]
    )

    result = runner.invoke(cli_module.app, ["--no-dry-run", "--fail-fast"])

    assert result.exit_code == EXIT_ERROR, result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    assert len(list(logs_dir.glob("*.json"))) == 1


def test_without_fail_fast_runs_every_discovered_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="a.yaml")
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="b.yaml")
    _queue_fake_run_container(
        monkeypatch, [(1, "boom"), (0, "destroyed"), (0, "ok"), (0, "ok"), (0, "ok")]
    )

    result = runner.invoke(cli_module.app, ["--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert "SUMMARY: 1 passed, 1 failed (of 2)" in result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    assert len(list(logs_dir.glob("*.json"))) == TWO_SCENARIOS


def test_no_scenarios_found_exits_with_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_module.app, ["--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output


def test_output_dir_cli_flag_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(
        monkeypatch, [(0, "ok"), (0, "ok"), (0, "Destroy complete!")]
    )

    result = runner.invoke(
        cli_module.app,
        [str(scenario_path), "--no-dry-run", "--output-dir", "custom-out"],
    )

    assert result.exit_code == EXIT_OK, result.output
    assert not (tmp_path / ".erv2-itests").exists()
    assert list((tmp_path / "custom-out" / "logs").glob("*.json"))


def test_scenarios_dir_cli_flag_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    custom_dir = tmp_path / "custom-scenarios"
    custom_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=custom_dir)
    _queue_fake_run_container(
        monkeypatch, [(0, "ok"), (0, "ok"), (0, "Destroy complete!")]
    )

    result = runner.invoke(
        cli_module.app, ["--no-dry-run", "--scenarios-dir", "custom-scenarios"]
    )

    assert result.exit_code == EXIT_OK, result.output


def test_config_file_sets_output_and_scenarios_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".erv2_itest.yml").write_text(
        "output_dir: from-config-out\nscenarios_dir: from-config-scenarios\n",
        encoding="utf-8",
    )
    scenarios_dir = tmp_path / "from-config-scenarios"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir)
    _queue_fake_run_container(
        monkeypatch, [(0, "ok"), (0, "ok"), (0, "Destroy complete!")]
    )

    result = runner.invoke(cli_module.app, ["--no-dry-run"])

    assert result.exit_code == EXIT_OK, result.output
    assert list((tmp_path / "from-config-out" / "logs").glob("*.json"))


def test_cli_output_dir_flag_beats_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".erv2_itest.yml").write_text(
        "output_dir: from-config-out\n", encoding="utf-8"
    )
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(
        monkeypatch, [(0, "ok"), (0, "ok"), (0, "Destroy complete!")]
    )

    result = runner.invoke(
        cli_module.app,
        [str(scenario_path), "--no-dry-run", "--output-dir", "from-cli-out"],
    )

    assert result.exit_code == EXIT_OK, result.output
    assert not (tmp_path / "from-config-out").exists()
    assert list((tmp_path / "from-cli-out" / "logs").glob("*.json"))


def test_terraform_mode_scenario_fails_cleanly_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(
        tmp_path,
        body="""
name: unit-test-terraform-scenario
mode: terraform
terraform:
  source: modules/my-module
steps:
  - name: apply
    expect:
      exit_code: 0
""",
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert "RESULT: FAIL" in result.output
    assert "not implemented yet" in result.output


def test_default_module_from_config_used_when_scenario_omits_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "credentials").write_text("fake", encoding="utf-8")
    (tmp_path / "base_input.json").write_text(
        json.dumps({"data": {}}), encoding="utf-8"
    )
    (tmp_path / ".erv2_itest.yml").write_text(
        "default_module:\n"
        "  image: shared-image:latest\n"
        "  credentials_file: credentials\n"
        "default_base_input: base_input.json\n",
        encoding="utf-8",
    )
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "name: no-module-scenario\n"
        "steps:\n"
        "  - name: apply\n"
        "    expect:\n"
        "      exit_code: 0\n",
        encoding="utf-8",
    )
    _queue_fake_run_container(monkeypatch, [(0, "ok")])

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_OK, result.output
    assert "RESULT: PASS" in result.output


def test_missing_module_and_no_config_default_falls_back_to_cwd_basename_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scenario with no `module:` at all previews cleanly.

    Uses a guessed image instead of erroring - see resolve_module().
    """
    monkeypatch.chdir(tmp_path)
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "name: no-module-scenario\n"
        "steps:\n"
        "  - name: apply\n"
        "    expect:\n"
        "      exit_code: 0\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--dry-run"])

    assert result.exit_code == EXIT_OK, result.output
    assert f"Target:   {tmp_path.name}:prod" in result.output


def test_missing_base_input_defaults_to_empty_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scenario with no base_input (and no default_base_input) still runs.

    Uses an empty base input template, relying purely on each step's own `input`,
    instead of erroring.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "credentials").write_text("fake", encoding="utf-8")
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "name: no-base-input-scenario\n"
        "module:\n"
        "  image: fake:latest\n"
        "  credentials_file: credentials\n"
        "steps:\n"
        "  - name: apply\n"
        "    input:\n"
        "      identifier: abc\n"
        "    expect:\n"
        "      exit_code: 0\n",
        encoding="utf-8",
    )
    _queue_fake_run_container(monkeypatch, [(0, "ok")])

    result = runner.invoke(
        cli_module.app, [str(scenario_path), "--no-dry-run", "--keep"]
    )

    assert result.exit_code == EXIT_OK, result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    run_id = json_path.stem
    input_path = tmp_path / ".erv2-itests" / "runs" / run_id / "input.json"
    assert json.loads(input_path.read_text(encoding="utf-8")) == {
        "data": {"identifier": "abc"}
    }


def test_dry_run_never_touches_vault_or_creates_credentials_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--dry-run must not call Vault or create .erv2-itests/credentials-cache/.

    Same "touches nothing" contract as Docker/AWS.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base_input.json").write_text(
        json.dumps({"data": {}}), encoding="utf-8"
    )

    def _fail_if_called(_path: str) -> dict[str, str]:
        raise AssertionError("dry-run must never call Vault")

    monkeypatch.setattr("erv2_itest.vault.fetch_kv_secret", _fail_if_called)

    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "name: no-credentials-file-scenario\n"
        "module:\n"
        "  image: fake:latest\n"
        "base_input: base_input.json\n"
        "steps:\n"
        "  - name: apply\n"
        "    expect:\n"
        "      exit_code: 0\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--dry-run"])

    assert result.exit_code == EXIT_OK, result.output
    assert not (tmp_path / ".erv2-itests").exists()


def test_vault_failure_is_reported_cleanly_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Vault CLI failure must be reported cleanly, not as a traceback.

    It's an expected, user-actionable setup error (not logged in, unreachable, ...).
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base_input.json").write_text(
        json.dumps({"data": {}}), encoding="utf-8"
    )

    def _fail_if_called(_path: str) -> dict[str, str]:
        raise RuntimeError(
            "Failed to read Vault secret at 'app-sre/creds/...': "
            "dial tcp 127.0.0.1:8200: connect: connection refused\n"
            "Is your local Vault session still valid? Try `vault login`."
        )

    monkeypatch.setattr("erv2_itest.vault.fetch_kv_secret", _fail_if_called)

    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "name: needs-vault-scenario\n"
        "module:\n"
        "  image: fake:latest\n"
        "base_input: base_input.json\n"
        "steps:\n"
        "  - name: apply\n"
        "    expect:\n"
        "      exit_code: 0\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "vault login" in result.output
    assert "Traceback" not in result.output


def test_terraform_missing_block_is_reported_cleanly_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        "name: no-terraform-block-scenario\n"
        "mode: terraform\n"
        "steps:\n"
        "  - name: apply\n"
        "    expect:\n"
        "      exit_code: 0\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli_module.app, [str(scenario_path), "--no-dry-run"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "requires a `terraform` block" in result.output
    assert "Traceback" not in result.output


def test_select_step_runs_only_the_cleanup_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`-k '::cleanup-step-name'` runs only that step, never the other steps.

    E.g. to manually re-run cleanup after a crash.
    """
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(monkeypatch, [(0, "Destroy complete!")])

    result = runner.invoke(
        cli_module.app, [str(scenario_path), "--no-dry-run", "-k", "::destroy"]
    )

    assert result.exit_code == EXIT_OK, result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    summary = json.loads(json_path.read_text(encoding="utf-8"))
    assert [s["name"] for s in summary["steps"]] == ["destroy"]


def test_select_step_runs_only_a_regular_step_and_skips_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(monkeypatch, [(0, "ok")])

    result = runner.invoke(
        cli_module.app, [str(scenario_path), "--no-dry-run", "-k", "::apply"]
    )

    assert result.exit_code == EXIT_OK, result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    [json_path] = logs_dir.glob("*.json")
    summary = json.loads(json_path.read_text(encoding="utf-8"))
    assert [s["name"] for s in summary["steps"]] == ["apply"]


def test_select_unknown_step_is_reported_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)

    result = runner.invoke(
        cli_module.app, [str(scenario_path), "-k", "::does-not-exist"]
    )

    assert result.exit_code != EXIT_OK
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "has no step named 'does-not-exist'" in result.output
    assert "Traceback" not in result.output


def test_run_id_reuses_the_given_id_instead_of_generating_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario_path = _write_scenario(tmp_path)
    _queue_fake_run_container(monkeypatch, [(0, "Destroy complete!")])

    result = runner.invoke(
        cli_module.app,
        [
            str(scenario_path),
            "--no-dry-run",
            "--run-id",
            "manual-recovery-id",
            "-k",
            "::destroy",
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    logs_dir = tmp_path / ".erv2-itests" / "logs"
    assert (logs_dir / "manual-recovery-id.json").exists()
    summary = json.loads((logs_dir / "manual-recovery-id.json").read_text())
    assert summary["run_id"] == "manual-recovery-id"
    # the (successful) destroy step's own work dir is cleaned up afterward
    assert not (tmp_path / ".erv2-itests" / "runs" / "manual-recovery-id").exists()


def test_run_id_with_multiple_scenarios_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="a.yaml")
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="b.yaml")

    result = runner.invoke(cli_module.app, ["--run-id", "shared-id"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert "only makes sense with exactly one scenario" in result.output


def test_single_colon_in_select_hints_at_double_colon_syntax(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A single ':' instead of '::' silently becomes a never-matching substring.

    That's a common typo, so hint at the fix instead of just reporting no
    scenarios found.
    """
    monkeypatch.chdir(tmp_path)
    scenarios_dir = tmp_path / "integration-tests"
    scenarios_dir.mkdir()
    _write_scenario(tmp_path, yaml_dir=scenarios_dir, filename="a.yaml")

    result = runner.invoke(cli_module.app, ["-k", "a:destroy"])

    assert result.exit_code == EXIT_ERROR, result.output
    assert "No scenario files found" in result.output
    assert "Hint:" in result.output
    assert "'::'" in result.output
