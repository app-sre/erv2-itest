"""Unit tests for the scenario YAML schema (pydantic models in erv2_itest.models)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from erv2_itest.models import (
    ModuleConfig,
    Scenario,
    ScenarioStep,
    StepExpectation,
    TerraformModuleConfig,
)

DEFAULT_TIMEOUT_SECONDS = 1800
CUSTOM_TIMEOUT_SECONDS = 60


def _minimal_scenario_dict() -> dict:
    return {
        "name": "my-scenario",
        "module": {"image": "my-image:latest", "credentials_file": "credentials"},
        "base_input": "base_input.json",
        "steps": [
            {"name": "apply", "expect": {"exit_code": 0}},
        ],
    }


def test_minimal_scenario_parses() -> None:
    scenario = Scenario.model_validate(_minimal_scenario_dict())

    assert scenario.name == "my-scenario"
    assert not scenario.description
    assert scenario.module is not None
    assert scenario.module.image == "my-image:latest"
    assert scenario.module.credentials_file == Path("credentials")
    assert scenario.base_input == Path("base_input.json")
    assert scenario.cleanup is None
    assert scenario.mode is None
    assert scenario.terraform is None


def test_step_defaults() -> None:
    scenario = Scenario.model_validate(_minimal_scenario_dict())
    step = scenario.steps[0]

    assert step.input == {}
    assert step.action == "Apply"
    assert step.dry_run is False
    assert step.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert step.expect.log_contains == []
    assert step.expect.log_not_contains == []


def test_full_scenario_with_cleanup_parses() -> None:
    raw = _minimal_scenario_dict()
    raw["description"] = "exercises the happy path"
    raw["steps"][0]["input"] = {"identifier": "abc"}
    raw["steps"][0]["dry_run"] = True
    raw["steps"][0]["timeout_seconds"] = CUSTOM_TIMEOUT_SECONDS
    raw["steps"][0]["expect"]["log_contains"] = ["Apply complete!"]
    raw["steps"][0]["expect"]["log_not_contains"] = ["Error:"]
    raw["cleanup"] = {
        "name": "destroy",
        "action": "Destroy",
        "expect": {"exit_code": 0},
    }

    scenario = Scenario.model_validate(raw)

    assert scenario.description == "exercises the happy path"
    assert scenario.steps[0].input == {"identifier": "abc"}
    assert scenario.steps[0].dry_run is True
    assert scenario.steps[0].timeout_seconds == CUSTOM_TIMEOUT_SECONDS
    assert scenario.steps[0].expect.log_contains == ["Apply complete!"]
    assert scenario.steps[0].expect.log_not_contains == ["Error:"]
    assert scenario.cleanup is not None
    assert scenario.cleanup.name == "destroy"
    assert scenario.cleanup.action == "Destroy"


@pytest.mark.parametrize("missing_field", ["name", "steps"])
def test_missing_required_field_rejected(missing_field: str) -> None:
    raw = _minimal_scenario_dict()
    del raw[missing_field]

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_module_and_base_input_are_optional_at_the_model_level() -> None:
    """module/base_input are optional here.

    cli.py enforces the per-mode requirement (falling back to .erv2_itest.yml's
    default_module/default_base_input) once the effective mode is known, not the
    schema itself.
    """
    scenario = Scenario.model_validate({
        "name": "my-scenario",
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })

    assert scenario.module is None
    assert scenario.base_input is None
    assert scenario.mode is None
    assert scenario.terraform is None


def test_mode_terraform_parses_with_terraform_block() -> None:
    scenario = Scenario.model_validate({
        "name": "my-terraform-scenario",
        "mode": "terraform",
        "terraform": {"source": "modules/my-module"},
        "steps": [{"name": "apply", "expect": {"exit_code": 0}}],
    })

    assert scenario.mode == "terraform"
    assert scenario.terraform is not None
    assert scenario.terraform.source == Path("modules/my-module")
    assert scenario.terraform.var_file is None


def test_invalid_mode_rejected() -> None:
    raw = _minimal_scenario_dict()
    raw["mode"] = "ansible"

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_module_config_container_engine_and_extra_env_defaults() -> None:
    module = ModuleConfig(image="my-image:latest", credentials_file=Path("credentials"))

    assert module.container_engine is None
    assert module.extra_env == {}


def test_module_config_invalid_container_engine_rejected() -> None:
    with pytest.raises(ValidationError):
        ModuleConfig.model_validate({
            "image": "my-image:latest",
            "credentials_file": "credentials",
            "container_engine": "kubectl",
        })


def test_module_config_accepts_extra_env() -> None:
    module = ModuleConfig.model_validate({
        "image": "my-image:latest",
        "credentials_file": "credentials",
        "extra_env": {"FOO": "bar"},
    })

    assert module.extra_env == {"FOO": "bar"}


def test_terraform_module_config_requires_source() -> None:
    with pytest.raises(ValidationError):
        TerraformModuleConfig.model_validate({"var_file": "terraform.tfvars"})


def test_extra_field_on_scenario_rejected() -> None:
    raw = _minimal_scenario_dict()
    raw["unexpected"] = "field"

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_extra_field_on_module_rejected() -> None:
    raw = _minimal_scenario_dict()
    raw["module"]["unexpected"] = "field"

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_extra_field_on_step_rejected() -> None:
    raw = _minimal_scenario_dict()
    raw["steps"][0]["unexpected"] = "field"

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_extra_field_on_expect_rejected() -> None:
    raw = _minimal_scenario_dict()
    raw["steps"][0]["expect"]["unexpected"] = "field"

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_invalid_action_rejected() -> None:
    raw = _minimal_scenario_dict()
    raw["steps"][0]["action"] = "Update"

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_module_config_requires_image() -> None:
    with pytest.raises(ValidationError):
        ModuleConfig.model_validate({"credentials_file": "credentials"})


def test_module_config_credentials_file_and_vault_fields_are_optional() -> None:
    """credentials_file may be omitted here.

    erv2-itest then generates one from Vault using target_account/tf_state_account
    (module-level here, or the config-file default) - see vault.py.
    """
    module = ModuleConfig.model_validate({"image": "my-image:latest"})

    assert module.credentials_file is None
    assert module.target_account is None
    assert module.tf_state_account is None


def test_step_expectation_requires_exit_code() -> None:
    with pytest.raises(ValidationError):
        StepExpectation.model_validate({"log_contains": ["foo"]})


def test_scenario_step_requires_expect() -> None:
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({"name": "apply"})
