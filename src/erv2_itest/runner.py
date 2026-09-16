"""Pluggable execution backends for a scenario step.

Today only `erv2` (docker run against an ERv2-wrapped module image) is implemented.
`terraform` (a plain Terraform module, no ERv2 wrapper) is architecture-only for now -
get_runner() resolves it to a stub that reports a clean failure instead of executing
anything, so scenarios can be authored and previewed ahead of that work landing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .docker_runner import run_container
from .logging_utils import get_logger
from .models import ModuleConfig

if TYPE_CHECKING:
    from pathlib import Path

    from .models import Mode, ScenarioStep, TerraformModuleConfig

logger = get_logger()


class Runner(Protocol):
    """Executes a single scenario step and returns (exit_code, combined_output)."""

    def run(
        self,
        *,
        module: ModuleConfig | TerraformModuleConfig,
        step: ScenarioStep,
        input_path: Path,
        work_dir: Path,
        container_name: str,
    ) -> tuple[int, str]: ...


class DockerErv2Runner:
    """Runs an ERv2 module image via docker/podman (today's, and the default, behavior)."""

    @staticmethod
    def run(
        *,
        module: ModuleConfig | TerraformModuleConfig,
        step: ScenarioStep,
        input_path: Path,
        work_dir: Path,
        container_name: str,
    ) -> tuple[int, str]:
        if not isinstance(module, ModuleConfig):
            msg = "DockerErv2Runner requires a ModuleConfig (mode=erv2)"
            raise TypeError(msg)
        if module.credentials_file is None:
            msg = "DockerErv2Runner requires a resolved credentials_file - resolve_module() must fill it in"
            raise ValueError(msg)
        return run_container(
            image=module.image,
            input_file=input_path,
            credentials_file=module.credentials_file,
            work_dir=work_dir,
            action=step.action,
            dry_run=step.dry_run,
            container_name=container_name,
            timeout_seconds=step.timeout_seconds,
            engine=module.container_engine,
            extra_env=module.extra_env,
        )


class TerraformRunner:
    """Stub: mode=terraform is architecture-only for now, not yet executable."""

    @staticmethod
    def run(
        *,
        module: ModuleConfig | TerraformModuleConfig,  # ruff: ignore[unused-static-method-argument]
        step: ScenarioStep,  # ruff: ignore[unused-static-method-argument]
        input_path: Path,  # ruff: ignore[unused-static-method-argument]
        work_dir: Path,  # ruff: ignore[unused-static-method-argument]
        container_name: str,  # ruff: ignore[unused-static-method-argument]
    ) -> tuple[int, str]:
        message = "Terraform mode is not implemented yet"
        logger.info("    %s", message)
        return 1, message


def get_runner(mode: Mode) -> Runner:
    """Pick the Runner implementation for a scenario's resolved mode."""
    if mode == "erv2":
        return DockerErv2Runner()
    if mode == "terraform":
        return TerraformRunner()
    msg = f"Unknown mode: {mode!r}"
    raise ValueError(msg)
