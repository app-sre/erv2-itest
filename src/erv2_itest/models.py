"""Pydantic models for erv2_itest scenario definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Action = Literal["Apply", "Destroy"]
Mode = Literal["erv2", "terraform"]


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
    """The ERv2 module image and credentials to test against (mode: erv2).

    `credentials_file` is an optional manual override - a plaintext AWS credentials
    file already on disk. When omitted, erv2-itest generates one from Vault using
    `target_account`/`tf_state_account` (module-level here, falling back to
    .erv2_itest.yml's config-level defaults) - see vault.py.
    """

    model_config = ConfigDict(extra="forbid")

    image: str
    credentials_file: Path | None = None
    target_account: str | None = None
    tf_state_account: str | None = None
    container_engine: Literal["docker", "podman"] | None = None
    extra_env: dict[str, str] = Field(default_factory=dict)


class TerraformModuleConfig(BaseModel):
    """A plain Terraform module to test, without the ERv2 docker wrapper (mode: terraform).

    Execution is not implemented yet - see TerraformRunner in runner.py - but the
    schema is real so scenarios can be authored and previewed (--dry-run) now.
    """

    model_config = ConfigDict(extra="forbid")

    source: Path
    var_file: Path | None = None


class Scenario(BaseModel):
    """A full integration-test scenario: a module, a base input, and a step sequence."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    mode: Mode | None = None
    module: ModuleConfig | None = None
    terraform: TerraformModuleConfig | None = None
    base_input: Path | None = None
    steps: list[ScenarioStep]
    cleanup: ScenarioStep | None = None
