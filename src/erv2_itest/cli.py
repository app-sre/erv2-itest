"""Unattended integration-test runner for ERv2 modules.

Add this package as a dev dependency of the CONSUMING module repo (e.g.
er-aws-elasticache) and run it from that repo's root, since the scenario YAML's own
paths (credentials_file, base_input) - and the .erv2-itests/ output directory - are
CWD-relative, not relative to this package's own install location:

    uv add --dev erv2-itest
    uv run erv2-itest integration-tests/<scenario>.yaml
    uv run erv2-itest integration-tests/<scenario>.yaml --no-dry-run

Defaults to --dry-run: resolves and prints the scenario without touching Docker/AWS at
all. Pass --no-dry-run to actually run it. Exits 0 only when every step (and cleanup)
passed - safe for an agent or CI to check via the process exit code alone.

Every real run is persisted under .erv2-itests/logs/<run_id>.log (full transcript) and
.erv2-itests/logs/<run_id>.json (structured per-step summary), independent of --keep, so
it can be reviewed later. See AGENTS.md for details.
"""

from __future__ import annotations

import json
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from external_resources_io.exit_status import EXIT_ERROR, EXIT_OK

from .docker_runner import run_container
from .logging_utils import RunRecord, StepRecord, configure_logging, get_logger, now_iso
from .models import Scenario, ScenarioStep
from .report import log_final, log_step

# Handlers are (re-)configured at the top of main(), bound to sys.stdout as it
# is *then* - not at import time. Binding once at import time would freeze the
# StreamHandler to whatever sys.stdout was before anything (e.g. a test
# runner) ever redirects it, silently sending all log output to the wrong
# stream. logger itself is a stable reference (logging.getLogger is a
# singleton lookup), so this reference stays valid after main() reconfigures it.
logger = get_logger()


def generate_run_id() -> str:
    """Generate an AWS-identifier-safe run ID that sorts in creation order.

    A UTC timestamp prefix (not a plain random token) means run IDs sort
    chronologically as plain strings - `ls`, `sort`, or eyeballing
    .erv2-itests/logs/ immediately shows which run came first. The random
    suffix only disambiguates two runs started within the same second.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"erv2it-{timestamp}-{secrets.token_hex(2)}"


def output_dir() -> Path:
    """Root directory for all run artifacts (logs/, runs/), relative to the invoking CWD.

    Deliberately Path.cwd() at call time, not a module-level constant computed at
    import time - matches the documented cross-repo usage (cd into the consuming
    module repo, then invoke erv2-itest), so output lands in *that* repo's
    .erv2-itests/, not wherever this package happens to be installed.
    """
    return Path.cwd() / ".erv2-itests"


def load_scenario(path: Path) -> Scenario:
    """Load and validate a scenario YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario.model_validate(raw)


def resolve_relative(path: Path) -> Path:
    """Resolve a scenario-relative path against the current working directory.

    Matches this repo's existing convention of running commands (make targets,
    docker run examples) from the repo root with $PWD-relative paths.
    """
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def resolve_base_input(scenario: Scenario, run_id: str) -> dict[str, Any]:
    """Load the scenario's base input JSON, substituting {{run_id}} everywhere."""
    text = resolve_relative(scenario.base_input).read_text(encoding="utf-8")
    result: dict[str, Any] = json.loads(text.replace("{{run_id}}", run_id))
    return result


def run_step(
    step: ScenarioStep,
    *,
    current_input: dict[str, Any],
    image: str,
    credentials_file: Path,
    work_dir: Path,
    container_name: str,
) -> tuple[bool, dict[str, Any], str, int]:
    """Merge overrides, run the container, check expectations against the result."""
    merged = dict(current_input)
    merged["data"] = {**merged.get("data", {}), **step.input}

    input_path = work_dir / "input.json"
    input_path.write_text(json.dumps(merged), encoding="utf-8")

    exit_code, output = run_container(
        image=image,
        input_file=input_path,
        credentials_file=credentials_file,
        work_dir=work_dir,
        action=step.action,
        dry_run=step.dry_run,
        container_name=container_name,
        timeout_seconds=step.timeout_seconds,
    )

    failure_reason = ""
    if exit_code != step.expect.exit_code:
        failure_reason = f"expected exit code {step.expect.exit_code}, got {exit_code}"
    for needle in step.expect.log_contains:
        if failure_reason:
            break
        if needle not in output:
            failure_reason = (
                f"expected log output to contain {needle!r}, but it did not"
            )
    for needle in step.expect.log_not_contains:
        if failure_reason:
            break
        if needle in output:
            failure_reason = (
                f"expected log output to NOT contain {needle!r}, but it did"
            )

    return (not failure_reason, merged, failure_reason, exit_code)


def container_name_for(run_id: str, step_name: str) -> str:
    """A docker/podman-safe container name derived from the run ID and step name."""
    slug = "".join(c if c.isalnum() else "-" for c in step_name.lower())
    return f"{run_id}-{slug}"[:63]


def log_dry_preview(scenario: Scenario) -> None:
    """Log what would run, without touching Docker/AWS."""
    logger.info(
        "\n--dry-run (default): nothing will run against Docker/AWS. Steps that would run:"
    )
    for i, step in enumerate(scenario.steps, 1):
        logger.info(
            "  %s. %s (action=%s, dry_run=%s, expect_exit=%s)",
            i,
            step.name,
            step.action,
            step.dry_run,
            step.expect.exit_code,
        )
    if scenario.cleanup:
        logger.info(
            "  cleanup. %s (action=%s)", scenario.cleanup.name, scenario.cleanup.action
        )


def run_and_record(
    step: ScenarioStep,
    *,
    current_input: dict[str, Any],
    image: str,
    credentials_file: Path,
    work_dir: Path,
    run_id: str,
    record: RunRecord,
) -> tuple[bool, dict[str, Any], str]:
    """Run one step, log its result, and append it to the structured run record."""
    ok, merged, reason, exit_code = run_step(
        step,
        current_input=current_input,
        image=image,
        credentials_file=credentials_file,
        work_dir=work_dir,
        container_name=container_name_for(run_id, step.name),
    )
    log_step(step.name, passed=ok, reason=reason)
    record.steps.append(
        StepRecord(
            name=step.name,
            action=step.action,
            dry_run=step.dry_run,
            expected_exit_code=step.expect.exit_code,
            actual_exit_code=exit_code,
            passed=ok,
            reason=reason,
        )
    )
    return ok, merged, reason


app = typer.Typer(add_completion=False)


@app.command()
def main(
    scenario: Annotated[Path, typer.Argument(help="Path to the scenario YAML file.")],
    *,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Preview the scenario without touching Docker/AWS. Pass --no-dry-run to actually run it.",
        ),
    ] = True,
    keep: Annotated[
        bool,
        typer.Option(
            "--keep",
            help="Skip cleanup (Destroy) and keep the run's working directory, for post-mortem debugging.",
        ),
    ] = False,
) -> None:
    """Run (or preview) an ERv2 module integration-test scenario, unattended."""
    parsed_scenario = load_scenario(scenario)
    run_id = generate_run_id()
    current_input = resolve_base_input(parsed_scenario, run_id)

    if dry_run:
        configure_logging(log_path=None)
        logger.info("Scenario: %s", parsed_scenario.name)
        logger.info("Run ID:   %s", run_id)
        logger.info("Image:    %s", parsed_scenario.module.image)
        log_dry_preview(parsed_scenario)
        raise typer.Exit(code=EXIT_OK)

    logs_dir = output_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_id}.log"
    json_path = logs_dir / f"{run_id}.json"
    configure_logging(log_path=log_path)

    logger.info("Scenario: %s", parsed_scenario.name)
    logger.info("Run ID:   %s", run_id)
    logger.info("Image:    %s", parsed_scenario.module.image)
    logger.info("Log:      %s", log_path)
    logger.info("Summary:  %s", json_path)

    record = RunRecord(
        scenario_name=parsed_scenario.name,
        run_id=run_id,
        image=parsed_scenario.module.image,
        started_at=now_iso(),
    )

    work_dir = output_dir() / "runs" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    credentials_file = resolve_relative(parsed_scenario.module.credentials_file)

    passed = True
    failure_reason = ""
    failed_step_name = ""

    for step in parsed_scenario.steps:
        ok, current_input, reason = run_and_record(
            step,
            current_input=current_input,
            image=parsed_scenario.module.image,
            credentials_file=credentials_file,
            work_dir=work_dir,
            run_id=run_id,
            record=record,
        )
        if not ok:
            passed, failure_reason, failed_step_name = False, reason, step.name
            break

    if parsed_scenario.cleanup and not keep:
        ok, current_input, reason = run_and_record(
            parsed_scenario.cleanup,
            current_input=current_input,
            image=parsed_scenario.module.image,
            credentials_file=credentials_file,
            work_dir=work_dir,
            run_id=run_id,
            record=record,
        )
        if not ok and passed:
            passed, failure_reason, failed_step_name = (
                False,
                reason,
                parsed_scenario.cleanup.name,
            )
    elif keep:
        logger.info(
            "\n--keep given: skipping cleanup. Working dir preserved: %s", work_dir
        )

    if passed and not keep:
        shutil.rmtree(work_dir, ignore_errors=True)

    record.passed = passed
    record.finished_at = now_iso()
    json_path.write_text(record.to_json(), encoding="utf-8")

    log_final(
        parsed_scenario.name,
        passed=passed,
        failed_step=failed_step_name,
        reason=failure_reason,
    )
    raise typer.Exit(code=EXIT_OK if passed else EXIT_ERROR)


if __name__ == "__main__":
    app()
