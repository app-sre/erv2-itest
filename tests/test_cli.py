"""Unit tests for erv2_itest.cli's pure functions (no Docker involved)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from erv2_itest.cli import (
    container_name_for,
    ensure_gitignore_entry,
    find_step,
    generate_run_id,
    load_scenario,
    parse_select,
    resolve_base_input,
    resolve_base_input_path,
    resolve_module,
    resolve_relative,
    run_step,
)
from erv2_itest.config import ErvItestConfig
from erv2_itest.models import ModuleConfig, Scenario, ScenarioStep
from erv2_itest.runner import DockerErv2Runner

RUN_ID_RE = re.compile(r"^erv2it-\d{14}-[0-9a-f]{4}$")
CONTAINER_NAME_MAX_LEN = 63


def test_generate_run_id_matches_expected_format() -> None:
    assert RUN_ID_RE.match(generate_run_id())


def test_generate_run_id_is_unique_across_calls() -> None:
    assert generate_run_id() != generate_run_id()


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

    with pytest.raises(Exception, match="steps"):
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

    result = resolve_base_input(Path("base_input.json"), "erv2it-20260101000000-abcd")

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
    run_id: str = "erv2it-test",
) -> tuple[bool, dict[str, Any], str, int]:
    def _fake_run_container(**_: object) -> tuple[int, str]:
        return exit_code, output

    monkeypatch.setattr("erv2_itest.runner.run_container", _fake_run_container)

    module = ModuleConfig(
        image="my-image:latest", credentials_file=tmp_path / "credentials"
    )

    return run_step(
        step,
        runner=DockerErv2Runner(),
        module=module,
        current_input={"data": {}},
        work_dir=tmp_path,
        container_name="erv2it-test-apply",
        run_id=run_id,
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


def test_run_step_substitutes_run_id_in_step_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A step's own `input:` values must get the same {{run_id}} treatment as base_input.

    A real scenario broke in production because a step overrode a value like
    `parameter_group_name: "{{run_id}}-custom-pg"` and the literal, unsubstituted
    placeholder text was sent straight to AWS - which rejected it (curly braces
    aren't valid identifier characters). Only `base_input`'s *file text* was ever
    substituted; step.input, being an already-parsed dict merged in afterward,
    was never touched.
    """
    step = _make_step(
        input={
            "parameter_group_name": "{{run_id}}-custom-pg",
            "nested": {"identifier": "{{run_id}}-child"},
            "tags": ["{{run_id}}-a", "{{run_id}}-b"],
            "replica_count": 2,
        },
        expect={"exit_code": 0},
    )

    _ok, merged, _reason, _exit_code = _run_step_with_fake_container(
        monkeypatch, tmp_path, step, exit_code=0, output="", run_id="erv2it-abc123"
    )

    assert merged["data"] == {
        "parameter_group_name": "erv2it-abc123-custom-pg",
        "nested": {"identifier": "erv2it-abc123-child"},
        "tags": ["erv2it-abc123-a", "erv2it-abc123-b"],
        "replica_count": 2,
    }


def test_run_step_writes_merged_input_json_to_work_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    step = _make_step(input={"engine_version": "7.1"}, expect={"exit_code": 0})

    _run_step_with_fake_container(monkeypatch, tmp_path, step, exit_code=0, output="")

    written = json.loads((tmp_path / "input.json").read_text(encoding="utf-8"))
    assert written["data"] == {"engine_version": "7.1"}


def _scenario_without_module_or_terraform(**overrides: object) -> Scenario:
    defaults: dict[str, object] = {
        "name": "my-scenario",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    }
    defaults.update(overrides)
    return Scenario.model_validate(defaults)


def test_resolve_module_erv2_uses_scenario_module_over_config_default(
    tmp_path: Path,
) -> None:
    scenario = Scenario.model_validate({
        "name": "my-scenario",
        "module": {"image": "scenario-image:latest", "credentials_file": "c"},
        "base_input": "b.json",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })
    config = ErvItestConfig(
        default_module=ModuleConfig(
            image="config-image:latest", credentials_file=Path("c")
        )
    )

    module = resolve_module(
        scenario, "erv2", config, output_dir=tmp_path, refresh_credentials=False
    )

    assert isinstance(module, ModuleConfig)
    assert module.image == "scenario-image:latest"


def test_resolve_module_erv2_falls_back_to_config_default(tmp_path: Path) -> None:
    scenario = _scenario_without_module_or_terraform()
    config = ErvItestConfig(
        default_module=ModuleConfig(
            image="config-image:latest", credentials_file=Path("credentials")
        )
    )

    module = resolve_module(
        scenario, "erv2", config, output_dir=tmp_path, refresh_credentials=False
    )

    assert isinstance(module, ModuleConfig)
    assert module.image == "config-image:latest"


def test_resolve_module_erv2_defaults_image_to_cwd_basename_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No `module:` at all, and no `default_module` in config, still resolves.

    Falls back to `<current directory name>:prod` instead of erroring out.
    """
    monkeypatch.chdir(tmp_path)
    scenario = _scenario_without_module_or_terraform()
    config = ErvItestConfig()

    module = resolve_module(
        scenario,
        "erv2",
        config,
        output_dir=tmp_path,
        refresh_credentials=False,
        resolve_credentials=False,
    )

    assert isinstance(module, ModuleConfig)
    assert module.image == f"{tmp_path.name}:prod"


def test_resolve_module_terraform_raises_without_scenario_or_config_terraform(
    tmp_path: Path,
) -> None:
    scenario = _scenario_without_module_or_terraform(mode="terraform")
    config = ErvItestConfig()

    with pytest.raises(ValueError, match=r"requires a `terraform` block"):
        resolve_module(
            scenario,
            "terraform",
            config,
            output_dir=tmp_path,
            refresh_credentials=False,
        )


def test_resolve_base_input_path_prefers_scenario_over_config() -> None:
    scenario = Scenario.model_validate({
        "name": "my-scenario",
        "base_input": "scenario_input.json",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })
    config = ErvItestConfig(default_base_input=Path("config_input.json"))

    assert resolve_base_input_path(scenario, config) == Path("scenario_input.json")


def test_resolve_base_input_path_falls_back_to_config_default() -> None:
    scenario = _scenario_without_module_or_terraform()
    config = ErvItestConfig(default_base_input=Path("config_input.json"))

    assert resolve_base_input_path(scenario, config) == Path("config_input.json")


def test_resolve_base_input_path_none_when_neither_is_set() -> None:
    scenario = _scenario_without_module_or_terraform()
    config = ErvItestConfig()

    assert resolve_base_input_path(scenario, config) is None


def test_resolve_module_erv2_generates_credentials_via_vault_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    generated = tmp_path / "generated.credentials"
    generated.write_text("[default]\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_get_or_create(**kwargs: object) -> Path:
        captured.update(kwargs)
        return generated

    monkeypatch.setattr(
        "erv2_itest.cli.get_or_create_credentials_file", _fake_get_or_create
    )

    scenario = Scenario.model_validate({
        "name": "my-scenario",
        "module": {
            "image": "my-image:latest",
            "target_account": "scenario-target",
        },
        "base_input": "base_input.json",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })
    config = ErvItestConfig()

    module = resolve_module(
        scenario, "erv2", config, output_dir=tmp_path, refresh_credentials=True
    )

    assert isinstance(module, ModuleConfig)
    assert module.credentials_file == generated
    assert captured["target_account"] == "scenario-target"
    assert captured["tf_state_account"] == config.tf_state_account
    assert captured["cache_dir"] == tmp_path / "credentials-cache"
    assert captured["refresh"] is True


def test_resolve_module_skips_vault_when_resolve_credentials_is_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--dry-run must never touch Vault, same as it never touches Docker/AWS."""

    def _fail_if_called(**_: object) -> Path:
        raise AssertionError("dry-run must never call Vault")

    monkeypatch.setattr(
        "erv2_itest.cli.get_or_create_credentials_file", _fail_if_called
    )

    scenario = Scenario.model_validate({
        "name": "my-scenario",
        "module": {"image": "my-image:latest"},
        "base_input": "base_input.json",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })
    config = ErvItestConfig()

    module = resolve_module(
        scenario,
        "erv2",
        config,
        output_dir=tmp_path,
        refresh_credentials=False,
        resolve_credentials=False,
    )

    assert isinstance(module, ModuleConfig)
    assert module.credentials_file is None
    assert not (tmp_path / "credentials-cache").exists()


def test_ensure_gitignore_entry_noop_outside_git_repo(tmp_path: Path) -> None:
    ensure_gitignore_entry(tmp_path, ".erv2-itests/")

    assert not (tmp_path / ".gitignore").exists()


def test_ensure_gitignore_entry_creates_gitignore_if_missing(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    ensure_gitignore_entry(tmp_path, ".erv2-itests/")

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == ".erv2-itests/\n"


def test_ensure_gitignore_entry_appends_to_existing_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")

    ensure_gitignore_entry(tmp_path, ".erv2-itests/")

    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "*.pyc\n.erv2-itests/\n"


def test_ensure_gitignore_entry_does_not_duplicate_existing_entry(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(".erv2-itests/\n", encoding="utf-8")

    ensure_gitignore_entry(tmp_path, ".erv2-itests/")

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == ".erv2-itests/\n"


@pytest.mark.parametrize(
    ("select", "expected"),
    [
        (None, (None, None)),
        ("", (None, None)),
        ("my-scenario", ("my-scenario", None)),
        ("my-scenario::cleanup", ("my-scenario", "cleanup")),
        ("::cleanup", (None, "cleanup")),
        ("my-scenario::", ("my-scenario", None)),
    ],
)
def test_parse_select(
    select: str | None, expected: tuple[str | None, str | None]
) -> None:
    assert parse_select(select) == expected


def _scenario_with_steps_and_cleanup() -> Scenario:
    return Scenario.model_validate({
        "name": "my-scenario",
        "module": {"image": "my-image:latest"},
        "steps": [
            {"name": "apply", "expect": {"exit_code": 0}},
            {"name": "apply again", "expect": {"exit_code": 0}},
        ],
        "cleanup": {"name": "destroy", "action": "Destroy", "expect": {"exit_code": 0}},
    })


def test_find_step_matches_a_regular_step() -> None:
    scenario = _scenario_with_steps_and_cleanup()

    step = find_step(scenario, "apply again")

    assert step.name == "apply again"


def test_find_step_matches_cleanup() -> None:
    scenario = _scenario_with_steps_and_cleanup()

    step = find_step(scenario, "destroy")

    assert step.name == "destroy"
    assert step.action == "Destroy"


def test_find_step_raises_clear_error_when_not_found() -> None:
    scenario = _scenario_with_steps_and_cleanup()

    with pytest.raises(ValueError, match=r"has no step named 'nope'"):
        find_step(scenario, "nope")
