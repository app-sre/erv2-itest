"""Regression coverage for docker_runner's interrupt handling.

A real incident: Ctrl-C during a `run.py` invocation left a real AWS
ElastiCache create running for 4+ minutes, undetected, because only the
local docker/podman CLI client process was killed (process.kill()) - not the
actual container running in the daemon, which for a non-detached `run` keeps
going even after its client dies.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from erv2_itest import docker_runner

if TYPE_CHECKING:
    from pathlib import Path

SIGKILL_EXIT_CODE = 137


def test_keyboard_interrupt_kills_the_container_by_name(tmp_path: Path) -> None:
    fake_process = MagicMock()
    fake_process.stdout = iter([])
    fake_process.wait.side_effect = [KeyboardInterrupt, SIGKILL_EXIT_CODE]

    with (
        patch("erv2_itest.docker_runner.subprocess.Popen", return_value=fake_process),
        patch("erv2_itest.docker_runner.subprocess.run") as mock_run,
        pytest.raises(KeyboardInterrupt),
    ):
        docker_runner.run_container(
            image="whatever:latest",
            input_file=tmp_path / "input.json",
            credentials_file=tmp_path / "credentials",
            work_dir=tmp_path,
            action="Apply",
            dry_run=False,
            container_name="my-container",
            timeout_seconds=10,
        )

    mock_run.assert_called_once()
    kill_cmd = mock_run.call_args.args[0]
    assert kill_cmd[:2] == [docker_runner.container_engine(), "kill"]
    assert "my-container" in kill_cmd
    fake_process.kill.assert_called_once()


def test_timeout_kills_the_container_by_name(tmp_path: Path) -> None:
    fake_process = MagicMock()
    fake_process.stdout = iter([])
    fake_process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="x", timeout=1),
        SIGKILL_EXIT_CODE,
    ]

    with (
        patch("erv2_itest.docker_runner.subprocess.Popen", return_value=fake_process),
        patch("erv2_itest.docker_runner.subprocess.run") as mock_run,
    ):
        exit_code, _output = docker_runner.run_container(
            image="whatever:latest",
            input_file=tmp_path / "input.json",
            credentials_file=tmp_path / "credentials",
            work_dir=tmp_path,
            action="Apply",
            dry_run=False,
            container_name="my-container",
            timeout_seconds=1,
        )

    assert exit_code == SIGKILL_EXIT_CODE
    mock_run.assert_called_once()
    kill_cmd = mock_run.call_args.args[0]
    assert kill_cmd[:2] == [docker_runner.container_engine(), "kill"]
    assert "my-container" in kill_cmd
    fake_process.kill.assert_called_once()


def test_image_created_at_returns_formatted_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _name: "/usr/bin/docker")
    fake_result = MagicMock(returncode=0, stdout="2026-09-10T12:34:56.789012345Z\n")

    with patch(
        "erv2_itest.docker_runner.subprocess.run", return_value=fake_result
    ) as mock_run:
        built = docker_runner.image_created_at("my-module:prod", engine="docker")

    assert built == "2026-09-10 12:34:56 UTC"
    inspect_cmd = mock_run.call_args.args[0]
    assert inspect_cmd == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Created}}",
        "my-module:prod",
    ]


def test_image_created_at_returns_empty_string_on_missing_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _name: "/usr/bin/docker")
    fake_result = MagicMock(returncode=1, stdout="")

    with patch("erv2_itest.docker_runner.subprocess.run", return_value=fake_result):
        built = docker_runner.image_created_at("missing:latest", engine="docker")

    assert built is not None
    assert not built


def test_image_created_at_returns_none_when_engine_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _name: None)

    built = docker_runner.image_created_at("my-module:prod", engine="docker")

    assert built is None


def test_image_created_at_returns_raw_string_on_unparseable_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _name: "/usr/bin/docker")
    fake_result = MagicMock(returncode=0, stdout="not-a-timestamp\n")

    with patch("erv2_itest.docker_runner.subprocess.run", return_value=fake_result):
        built = docker_runner.image_created_at("my-module:prod", engine="docker")

    assert built == "not-a-timestamp"
