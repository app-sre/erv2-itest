"""Runs an ERv2 module's docker image as a subprocess, streaming and capturing output."""

from __future__ import annotations

import shutil
import subprocess
import threading
from typing import TYPE_CHECKING

from .logging_utils import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger()


def container_engine() -> str:
    """Pick podman if available, else docker - mirrors this repo's Makefile."""
    return "podman" if shutil.which("podman") else "docker"


def _kill_container(container_name: str, *, engine: str) -> None:
    """Kill the actual container by name, using the same engine it was started with.

    Killing the local docker/podman CLI client process (e.g. process.kill())
    is NOT enough: for a non-detached `run`, the container itself keeps
    running in the daemon even if the client that started it dies - this is
    exactly what left a real AWS create running for 4+ minutes after a
    Ctrl-C, undetected, until it was noticed separately.
    """
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [engine, "kill", container_name],
        check=False,
        capture_output=True,
    )


def run_container(
    *,
    image: str,
    input_file: Path,
    credentials_file: Path,
    work_dir: Path,
    action: str,
    dry_run: bool,
    container_name: str,
    timeout_seconds: int,
    engine: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run the module image, streaming output live and returning (exit_code, combined_output).

    Deliberately omits `-it`: allocating a TTY fails or hangs when there isn't
    one, which is always the case for an unattended/agent-driven run.
    """
    resolved_engine = engine or container_engine()
    cmd = [
        resolved_engine,
        "run",
        "--rm",
        "--name",
        container_name,
        "--mount",
        f"type=bind,source={input_file},target=/inputs/input.json",
        "--mount",
        f"type=bind,source={credentials_file},target=/credentials",
        "--mount",
        f"type=bind,source={work_dir},target=/work",
        "-e",
        f"DRY_RUN={dry_run}",
        "-e",
        f"ACTION={action}",
    ]
    for key, value in (extra_env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd.append(image)
    logger.info("    $ %s", " ".join(cmd))

    lines: list[str] = []
    process = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true]
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logger.info("    %s", line.rstrip("\n"))
            lines.append(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        logger.info(
            "    [erv2_itest] TIMEOUT after %ss, killing container %s",
            timeout_seconds,
            container_name,
        )
        _kill_container(container_name, engine=resolved_engine)
        process.kill()
        exit_code = process.wait()
    except KeyboardInterrupt:
        logger.info(
            "    [erv2_itest] Interrupted, killing container %s", container_name
        )
        _kill_container(container_name, engine=resolved_engine)
        process.kill()
        process.wait()
        reader.join(timeout=5)
        raise

    reader.join(timeout=5)
    return exit_code, "".join(lines)
