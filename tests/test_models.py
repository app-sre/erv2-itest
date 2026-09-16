"""Unit tests for the scenario YAML schema (pydantic models in erv2_itest.models)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from erv2_itest.models import ModuleConfig, Scenario, ScenarioStep, StepExpectation

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
    assert scenario.module.image == "my-image:latest"
    assert scenario.module.credentials_file == Path("credentials")
    assert scenario.base_input == Path("base_input.json")
    assert scenario.cleanup is None


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


@pytest.mark.parametrize("missing_field", ["name", "module", "base_input", "steps"])
def test_missing_required_field_rejected(missing_field: str) -> None:
    raw = _minimal_scenario_dict()
    del raw[missing_field]

    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


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


def test_module_config_requires_both_fields() -> None:
    with pytest.raises(ValidationError):
        ModuleConfig.model_validate({"image": "my-image:latest"})


def test_step_expectation_requires_exit_code() -> None:
    with pytest.raises(ValidationError):
        StepExpectation.model_validate({"log_contains": ["foo"]})


def test_scenario_step_requires_expect() -> None:
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({"name": "apply"})
