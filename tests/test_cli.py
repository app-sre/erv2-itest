"""Unit tests for erv2_itest.cli's pure functions (no Docker involved)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from erv2_itest.cli import (
    container_name_for,
    generate_run_id,
    load_scenario,
    output_dir,
    resolve_base_input,
    resolve_relative,
    run_step,
)
from erv2_itest.models import Scenario, ScenarioStep

RUN_ID_RE = re.compile(r"^erv2it-\d{14}-[0-9a-f]{4}$")
CONTAINER_NAME_MAX_LEN = 63


def test_generate_run_id_matches_expected_format() -> None:
    assert RUN_ID_RE.match(generate_run_id())


def test_generate_run_id_is_unique_across_calls() -> None:
    assert generate_run_id() != generate_run_id()


def test_output_dir_is_erv2_itests_under_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    assert output_dir() == tmp_path / ".erv2-itests"


def test_load_scenario_parses_valid_yaml(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        """
        name: my-scenario
        module:
          image: my-image:latest
          credentials_file: credentials
        base_input: base_input.json
        steps:
          - name: apply
            expect:
              exit_code: 0
        """,
        encoding="utf-8",
    )

    scenario = load_scenario(scenario_path)

    assert isinstance(scenario, Scenario)
    assert scenario.name == "my-scenario"


def test_load_scenario_rejects_invalid_yaml(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("name: my-scenario\n", encoding="utf-8")

    with pytest.raises(Exception, match="module"):
        load_scenario(scenario_path)


def test_resolve_relative_returns_absolute_path_unchanged(tmp_path: Path) -> None:
    absolute = tmp_path / "credentials"

    assert resolve_relative(absolute) == absolute


def test_resolve_relative_resolves_against_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_relative(Path("credentials")) == tmp_path / "credentials"


def test_resolve_base_input_substitutes_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    base_input_path = tmp_path / "base_input.json"
    base_input_path.write_text(
        json.dumps({"data": {"identifier": "{{run_id}}"}}), encoding="utf-8"
    )
    scenario = Scenario.model_validate({
        "name": "my-scenario",
        "module": {"image": "my-image:latest", "credentials_file": "credentials"},
        "base_input": "base_input.json",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })

    result = resolve_base_input(scenario, "erv2it-20260101000000-abcd")

    assert result == {"data": {"identifier": "erv2it-20260101000000-abcd"}}


@pytest.mark.parametrize(
    ("step_name", "expected_slug"),
    [
        ("loop 1", "loop-1"),
        ("apply/destroy!", "apply-destroy-"),
        ("Already-Slug", "already-slug"),
    ],
)
def test_container_name_for_slugifies_step_name(
    step_name: str, expected_slug: str
) -> None:
    assert (
        container_name_for("erv2it-test", step_name) == f"erv2it-test-{expected_slug}"
    )


def test_container_name_for_truncates_to_63_chars() -> None:
    name = container_name_for("erv2it-test", "x" * 100)

    assert len(name) == CONTAINER_NAME_MAX_LEN


def _make_step(**overrides: object) -> ScenarioStep:
    defaults: dict[str, object] = {
        "name": "apply",
        "expect": {"exit_code": 0},
    }
    defaults.update(overrides)
    return ScenarioStep.model_validate(defaults)


def _run_step_with_fake_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    step: ScenarioStep,
    *,
    exit_code: int,
    output: str,
) -> tuple[bool, dict[str, Any], str, int]:
    def _fake_run_container(**_: object) -> tuple[int, str]:
        return exit_code, output

    monkeypatch.setattr("erv2_itest.cli.run_container", _fake_run_container)

    return run_step(
        step,
        current_input={"data": {}},
        image="my-image:latest",
        credentials_file=tmp_path / "credentials",
        work_dir=tmp_path,
        container_name="erv2it-test-apply",
    )


def test_run_step_passes_when_exit_code_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(expect={"exit_code": 0})

    ok, _merged, reason, exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output="Apply complete!"
    )

    assert ok is True
    assert not reason
    assert exit_code == 0


def test_run_step_fails_when_exit_code_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(expect={"exit_code": 0})

    ok, _merged, reason, exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=1, output=""
    )

    assert ok is False
    assert reason == "expected exit code 0, got 1"
    assert exit_code == 1


def test_run_step_stops_checking_logs_once_exit_code_already_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(
        expect={
            "exit_code": 0,
            "log_contains": ["Apply complete!"],
            "log_not_contains": ["Error:"],
        }
    )

    ok, _merged, reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=1, output="Error: something broke"
    )

    assert ok is False
    assert reason == "expected exit code 0, got 1"


def test_run_step_fails_when_log_contains_needle_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(expect={"exit_code": 0, "log_contains": ["Apply complete!"]})

    ok, _merged, reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output="something else"
    )

    assert ok is False
    assert "Apply complete!" in reason


def test_run_step_passes_when_log_contains_needle_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(expect={"exit_code": 0, "log_contains": ["Apply complete!"]})

    ok, _merged, reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output="... Apply complete! ..."
    )

    assert ok is True
    assert not reason


def test_run_step_fails_when_log_not_contains_needle_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(expect={"exit_code": 0, "log_not_contains": ["Error:"]})

    ok, _merged, reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output="Error: something broke"
    )

    assert ok is False
    assert "Error:" in reason


def test_run_step_passes_when_log_not_contains_needle_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(expect={"exit_code": 0, "log_not_contains": ["Error:"]})

    ok, _merged, reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output="Apply complete!"
    )

    assert ok is True
    assert not reason


def test_run_step_merges_step_input_into_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(input={"engine_version": "7.1"}, expect={"exit_code": 0})

    _ok, merged, _reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output=""
    )

    assert merged["data"] == {"engine_version": "7.1"}


def test_run_step_writes_merged_input_json_to_work_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(input={"engine_version": "7.1"}, expect={"exit_code": 0})

    _run_step_with_fake_container(monkeypatch, tmp_path, step, exit_code=0, output="")

    written = json.loads((tmp_path / "input.json").read_text(encoding="utf-8"))
    assert written["data"] == {"engine_version": "7.1"}
