"""Unit tests for erv2_itest.runner's Runner dispatch and implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from erv2_itest.models import ModuleConfig, ScenarioStep, TerraformModuleConfig
from erv2_itest.runner import DockerErv2Runner, TerraformRunner, get_runner

if TYPE_CHECKING:
    from pathlib import Path


def test_get_runner_erv2_returns_docker_erv2_runner() -> None:
    assert isinstance(get_runner("erv2"), DockerErv2Runner)


def test_get_runner_terraform_returns_terraform_runner() -> None:
    assert isinstance(get_runner("terraform"), TerraformRunner)


def test_get_runner_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="Unknown mode"):
        get_runner("ansible")  # type: ignore[arg-type]


def test_docker_erv2_runner_calls_run_container_with_module_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def _fake_run_container(**kwargs: object) -> tuple[int, str]:
        captured.update(kwargs)
        return 0, "ok"

    monkeypatch.setattr("erv2_itest.runner.run_container", _fake_run_container)

    module = ModuleConfig(
        image="my-image:latest",
        credentials_file=tmp_path / "credentials",
        container_engine="podman",
        extra_env={"FOO": "bar"},
    )
    step = ScenarioStep.model_validate({"name": "apply", "expect": {"exit_code": 0}})

    exit_code, output = DockerErv2Runner().run(
        module=module,
        step=step,
        input_path=tmp_path / "input.json",
        work_dir=tmp_path,
        container_name="erv2it-test-apply",
    )

    assert (exit_code, output) == (0, "ok")
    assert captured["image"] == "my-image:latest"
    assert captured["engine"] == "podman"
    assert captured["extra_env"] == {"FOO": "bar"}


def test_docker_erv2_runner_rejects_terraform_module_config(tmp_path: Path) -> None:
    module = TerraformModuleConfig(source=tmp_path / "modules" / "my-module")
    step = ScenarioStep.model_validate({"name": "apply", "expect": {"exit_code": 0}})

    with pytest.raises(TypeError, match="requires a ModuleConfig"):
        DockerErv2Runner().run(
            module=module,
            step=step,
            input_path=tmp_path / "input.json",
            work_dir=tmp_path,
            container_name="erv2it-test-apply",
        )


def test_docker_erv2_runner_rejects_unresolved_credentials_file(tmp_path: Path) -> None:
    module = ModuleConfig(image="my-image:latest", credentials_file=None)
    step = ScenarioStep.model_validate({"name": "apply", "expect": {"exit_code": 0}})

    with pytest.raises(ValueError, match="resolved credentials_file"):
        DockerErv2Runner().run(
            module=module,
            step=step,
            input_path=tmp_path / "input.json",
            work_dir=tmp_path,
            container_name="erv2it-test-apply",
        )


def test_terraform_runner_returns_clean_failure_without_executing_anything(
    tmp_path: Path,
) -> None:
    module = TerraformModuleConfig(source=tmp_path / "modules" / "my-module")
    step = ScenarioStep.model_validate({"name": "apply", "expect": {"exit_code": 0}})

    exit_code, output = TerraformRunner().run(
        module=module,
        step=step,
        input_path=tmp_path / "input.json",
        work_dir=tmp_path,
        container_name="erv2it-test-apply",
    )

    assert exit_code == 1
    assert output == "Terraform mode is not implemented yet"
