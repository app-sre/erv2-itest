"""Pydantic models for erv2_itest scenario definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Action = Literal["Apply", "Destroy"]


class StepExpectation(BaseModel):
    """Expected outcome of a single docker run step."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    log_contains: list[str] = Field(default_factory=list)
    log_not_contains: list[str] = Field(default_factory=list)


class ScenarioStep(BaseModel):
    """A single `docker run` invocation within a scenario (one reconcile loop)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    action: Action = "Apply"
    dry_run: bool = False
    expect: StepExpectation
    timeout_seconds: int = 1800


class ModuleConfig(BaseModel):
    """The ERv2 module image and credentials to test against."""

    model_config = ConfigDict(extra="forbid")

    image: str
    credentials_file: Path


class Scenario(BaseModel):
    """A full integration-test scenario: a module, a base input, and a step sequence."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    module: ModuleConfig
    base_input: Path
    steps: list[ScenarioStep]
    cleanup: ScenarioStep | None = None
