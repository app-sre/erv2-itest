"""Shared fixtures for erv2-itest's own test suite."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from erv2_itest import cli as cli_module
from erv2_itest.docker_runner import container_engine

if TYPE_CHECKING:
    from collections.abc import Generator

TESTS_DIR = Path(__file__).resolve().parent
ERV2_ITEST_DIR = TESTS_DIR.parent
SMOKETEST_DIR = ERV2_ITEST_DIR / "smoketest"
FAKE_IMAGE = "erv2it-fake:latest"


@pytest.fixture(scope="session", autouse=True)  # ruff: ignore[pytest-fixture-autouse]
def _require_repo_root() -> None:
    """The smoketest scenario YAMLs use smoketest/... paths relative to this repo's root."""
    if not (Path.cwd() / "smoketest").exists():
        pytest.fail(
            "Run this suite from the erv2-itest repo root "
            "(smoketest/... paths in the smoketest scenarios are CWD-relative)."
        )


@pytest.fixture(scope="session")
def fake_image() -> Generator[str]:
    """Build the tiny fake ERv2-module image once for the whole test session.

    Skips (rather than fails) when neither docker nor podman is on PATH, so
    `make test` still passes in a container-build environment (e.g. the Tekton
    test image) that has no container engine of its own available.
    """
    if shutil.which("podman") is None and shutil.which("docker") is None:
        pytest.skip("no container engine (docker/podman) available")

    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [container_engine(), "build", "-q", "-t", FAKE_IMAGE, str(SMOKETEST_DIR)],
        check=True,
        capture_output=True,
    )
    yield FAKE_IMAGE
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [container_engine(), "rmi", FAKE_IMAGE], check=False, capture_output=True
    )


@pytest.fixture
def fixed_run_id(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Force a predictable-prefix, per-test-unique run ID for easy .erv2-itests/ cleanup.

    Unique per test invocation (not a shared literal) so that back-to-back tests
    never bind-mount the exact same host directory path for two real containers
    in a row - reusing one path across successive `docker run`s has been
    observed to trip a directory-visibility race in the container engine.
    """
    run_id = f"erv2it-pytest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(cli_module, "generate_run_id", lambda: run_id)
    yield run_id

    output_dir = Path.cwd() / ".erv2-itests"
    shutil.rmtree(output_dir / "runs" / run_id, ignore_errors=True)
    for suffix in (".log", ".json"):
        (output_dir / "logs" / f"{run_id}{suffix}").unlink(missing_ok=True)
