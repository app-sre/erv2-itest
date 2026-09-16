"""Unattended integration-test runner for ERv2 modules.

Add this package as a dev dependency of the CONSUMING module repo (e.g.
er-aws-elasticache) and run it from that repo's root, since scenario-relative paths,
the .erv2-itests/ output directory, and an optional .erv2_itest.yml config file are all
resolved against the invoking CWD, not this package's own install location:

    uv add --dev erv2-itest
    uv run erv2-itest                                    # auto-discover integration-tests/*.yaml, run for real
    uv run erv2-itest integration-tests/<scenario>.yaml --dry-run

Defaults to --no-dry-run: actually runs each scenario against Docker/AWS/Vault. Pass
--dry-run to only resolve and print what would happen, touching nothing. Exits 0 only
when every scenario (all its steps and cleanup) passed - safe for an agent or CI to
check via the process exit code alone.

Every real run is persisted under .erv2-itests/logs/<run_id>.log (full transcript) and
.erv2-itests/logs/<run_id>.json (structured per-step summary), independent of --keep, so
it can be reviewed later. See AGENTS.md for details.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from external_resources_io.exit_status import EXIT_ERROR, EXIT_OK

from .config import ErvItestConfig
from .logging_utils import RunRecord, StepRecord, configure_logging, get_logger, now_iso
from .models import Mode, ModuleConfig, Scenario, ScenarioStep, TerraformModuleConfig
from .report import log_final, log_step, log_summary
from .runner import Runner, get_runner
from .vault import get_or_create_credentials_file

# Handlers are (re-)configured per scenario run, bound to sys.stdout as it is *then* -
# not at import time. Binding once at import time would freeze the StreamHandler to
# whatever sys.stdout was before anything (e.g. a test runner) ever redirects it,
# silently sending all log output to the wrong stream. logger itself is a stable
# reference (logging.getLogger is a singleton lookup), so this reference stays valid
# after configure_logging() reconfigures it.
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


def resolve_base_input(base_input: Path, run_id: str) -> dict[str, Any]:
    """Load the base input JSON, substituting {{run_id}} everywhere."""
    text = resolve_relative(base_input).read_text(encoding="utf-8")
    result: dict[str, Any] = json.loads(text.replace("{{run_id}}", run_id))
    return result


def discover_scenarios(scenarios_dir: Path, *, select: str | None) -> list[Path]:
    """Find *.yaml/*.yml scenario files under scenarios_dir, sorted, optionally filtered.

    Recurses into subdirectories, so scenarios can be organized into folders (e.g. by
    module or feature area). `select` matches as a substring against the path
    relative to scenarios_dir (extension stripped), so a subdirectory name is
    matchable too, not just the filename.
    """
    if not scenarios_dir.is_dir():
        return []
    candidates = sorted({*scenarios_dir.rglob("*.yaml"), *scenarios_dir.rglob("*.yml")})
    if select:
        candidates = [
            path
            for path in candidates
            if select in path.relative_to(scenarios_dir).with_suffix("").as_posix()
        ]
    return candidates


def parse_select(select: str | None) -> tuple[str | None, str | None]:
    """Split -k/--select into (scenario_substring, step_name), either half optional.

    `scenario::step` mirrors pytest's `file::test` node-id convention, e.g.
    `-k "elasticache-noauth-to-auth::cleanup"` to manually re-run just the cleanup
    step of a matching scenario after a crash. A bare value with no `::` is
    scenario-substring-only, same as before this existed. Either side of `::` can be
    empty (e.g. `-k "::cleanup"` when an explicit scenario path is already given).
    """
    if not select:
        return None, None
    if "::" not in select:
        return select, None
    scenario_part, _, step_part = select.partition("::")
    return (scenario_part or None), (step_part or None)


def find_step(scenario: Scenario, step_name: str) -> ScenarioStep:
    """The scenario's step (or cleanup) matching step_name exactly."""
    for step in scenario.steps:
        if step.name == step_name:
            return step
    if scenario.cleanup and scenario.cleanup.name == step_name:
        return scenario.cleanup
    msg = (
        f"scenario {scenario.name!r} has no step named {step_name!r} "
        "(checked steps and cleanup)"
    )
    raise ValueError(msg)


def ensure_gitignore_entry(cwd: Path, entry: str) -> None:
    """Best-effort: make sure `entry` is listed in cwd's .gitignore, appending it if not.

    A no-op outside a git repo (no .git dir at cwd). Guards against accidentally
    committing generated run artifacts - including the Vault-derived credentials
    cache - into a consuming module's repo.
    """
    if not (cwd / ".git").is_dir():
        return

    gitignore_path = cwd / ".gitignore"
    existing = (
        gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    )
    entries = {line.strip().rstrip("/") for line in existing.splitlines()}
    if entry.rstrip("/") in entries:
        return

    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with gitignore_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}{entry}\n")


def resolve_module(
    scenario: Scenario,
    mode: Mode,
    config: ErvItestConfig,
    *,
    output_dir: Path,
    refresh_credentials: bool,
    resolve_credentials: bool = True,
) -> ModuleConfig | TerraformModuleConfig:
    """The scenario's own module block, falling back to the config file's default.

    Lets a repo whose scenarios all exercise the same module declare it once as
    `default_module`/`default_terraform` in .erv2_itest.yml instead of repeating an
    identical block in every scenario YAML. If neither gives an image, erv2-itest
    guesses `<current directory name>:prod` - a module's own repo is almost always
    named after the image it builds, and its own `Dockerfile`/CI already tags `prod` -
    rather than failing outright on a bare scenario with no `module:` at all.

    `resolve_credentials=False` (used for --dry-run) skips generating a Vault-backed
    credentials file - dry-run must never touch Vault, same as it never touches
    Docker/AWS.
    """
    if mode == "erv2":
        module = (
            scenario.module
            or config.default_module
            or ModuleConfig(image=f"{Path.cwd().name}:prod")
        )
        if not resolve_credentials:
            return module
        if module.credentials_file is not None:
            credentials_file = resolve_relative(module.credentials_file)
        else:
            credentials_file = get_or_create_credentials_file(
                target_account=module.target_account or config.target_account,
                tf_state_account=module.tf_state_account or config.tf_state_account,
                cache_dir=output_dir / "credentials-cache",
                refresh=refresh_credentials,
            )
        return ModuleConfig(
            image=module.image,
            credentials_file=credentials_file,
            container_engine=module.container_engine,
            extra_env=module.extra_env,
        )

    terraform = scenario.terraform or config.default_terraform
    if terraform is None:
        msg = (
            f"scenario {scenario.name!r}: mode=terraform requires a `terraform` block "
            "(in the scenario YAML, or `default_terraform` in .erv2_itest.yml)"
        )
        raise ValueError(msg)
    return terraform


def resolve_base_input_path(scenario: Scenario, config: ErvItestConfig) -> Path | None:
    """The scenario's own base_input, falling back to the config file's default."""
    return scenario.base_input or config.default_base_input


def run_step(
    step: ScenarioStep,
    *,
    runner: Runner,
    module: ModuleConfig | TerraformModuleConfig,
    current_input: dict[str, Any],
    work_dir: Path,
    container_name: str,
) -> tuple[bool, dict[str, Any], str, int]:
    """Merge overrides, run the step, check expectations against the result."""
    merged = dict(current_input)
    merged["data"] = {**merged.get("data", {}), **step.input}

    input_path = work_dir / "input.json"
    input_path.write_text(json.dumps(merged), encoding="utf-8")
    logger.debug("Step input: %s", json.dumps(merged))

    exit_code, output = runner.run(
        module=module,
        step=step,
        input_path=input_path,
        work_dir=work_dir,
        container_name=container_name,
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
        "\n--dry-run: nothing will run against Docker/AWS. Steps that would run:"
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
    runner: Runner,
    module: ModuleConfig | TerraformModuleConfig,
    current_input: dict[str, Any],
    work_dir: Path,
    run_id: str,
    record: RunRecord,
) -> tuple[bool, dict[str, Any], str]:
    """Run one step, log its result, and append it to the structured run record."""
    ok, merged, reason, exit_code = run_step(
        step,
        runner=runner,
        module=module,
        current_input=current_input,
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


def run_scenario(  # ruff: ignore[too-many-locals,too-many-statements,complex-structure]
    scenario_path: Path,
    *,
    output_dir: Path,
    dry_run: bool,
    keep: bool,
    config: ErvItestConfig,
    log_level: int,
    refresh_credentials: bool,
    run_id_override: str | None = None,
    only_step: str | None = None,
) -> bool:
    """Run (or preview) a single scenario file. Returns True if it passed.

    `only_step`, if given, runs just that one step (matched by name against both
    `steps` and `cleanup`) instead of the full sequence - e.g. to manually re-run
    `cleanup` after a crash left real resources orphaned - and skips the scenario's
    own automatic cleanup afterward (the point is to run exactly one named step, not
    a full scenario). `run_id_override` reuses a specific run_id (e.g. a crashed
    run's) instead of generating a fresh one, so `{{run_id}}`-derived identifiers in
    base_input still target the same resources.
    """
    parsed_scenario = load_scenario(scenario_path)
    target_step = find_step(parsed_scenario, only_step) if only_step else None
    effective_mode: Mode = parsed_scenario.mode or config.default_mode
    module = resolve_module(
        parsed_scenario,
        effective_mode,
        config,
        output_dir=output_dir,
        refresh_credentials=refresh_credentials,
        resolve_credentials=not dry_run,
    )
    runner = get_runner(effective_mode)

    base_input_path = resolve_base_input_path(parsed_scenario, config)

    target = module.image if isinstance(module, ModuleConfig) else str(module.source)

    run_id = run_id_override or generate_run_id()
    current_input = (
        resolve_base_input(base_input_path, run_id) if base_input_path else {}
    )

    if dry_run:
        configure_logging(log_path=None, level=log_level)
        logger.info("Scenario: %s", parsed_scenario.name)
        logger.info("Run ID:   %s", run_id)
        logger.info("Mode:     %s", effective_mode)
        logger.info("Target:   %s", target)
        if target_step is not None:
            logger.info(
                "\n--dry-run: nothing will run against Docker/AWS. Would "
                "run only: %s (action=%s, expect_exit=%s)",
                target_step.name,
                target_step.action,
                target_step.expect.exit_code,
            )
        else:
            log_dry_preview(parsed_scenario)
        return True

    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_id}.log"
    json_path = logs_dir / f"{run_id}.json"
    configure_logging(log_path=log_path, level=log_level)

    logger.info("Scenario: %s", parsed_scenario.name)
    logger.info("Run ID:   %s", run_id)
    logger.info("Mode:     %s", effective_mode)
    logger.info("Target:   %s", target)
    if target_step is not None:
        logger.info("Step:     %s", target_step.name)
    logger.info("Log:      %s", log_path)
    logger.info("Summary:  %s", json_path)

    record = RunRecord(
        scenario_name=parsed_scenario.name,
        run_id=run_id,
        image=target,
        started_at=now_iso(),
    )

    work_dir = output_dir / "runs" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)

    passed = True
    failure_reason = ""
    failed_step_name = ""

    steps_to_run = [target_step] if target_step is not None else parsed_scenario.steps
    for step in steps_to_run:
        ok, current_input, reason = run_and_record(
            step,
            runner=runner,
            module=module,
            current_input=current_input,
            work_dir=work_dir,
            run_id=run_id,
            record=record,
        )
        if not ok:
            passed, failure_reason, failed_step_name = False, reason, step.name
            break

    if target_step is None:
        if parsed_scenario.cleanup and not keep:
            ok, current_input, reason = run_and_record(
                parsed_scenario.cleanup,
                runner=runner,
                module=module,
                current_input=current_input,
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
    return passed


app = typer.Typer()


@app.command()
def main(
    scenarios: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Scenario YAML file(s) to run. Omit to auto-discover every scenario "
            "in --scenarios-dir, like a test suite."
        ),
    ] = None,
    *,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Actually runs scenario(s) against Docker/AWS/Vault by default. Pass --dry-run to only preview them, touching nothing.",
        ),
    ] = False,
    keep: Annotated[
        bool,
        typer.Option(
            "--keep",
            help="Skip cleanup (Destroy) and keep the run's working directory, for post-mortem debugging.",
        ),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Override the .erv2-itests/-equivalent output directory (default: config file, then .erv2-itests).",
        ),
    ] = None,
    scenarios_dir: Annotated[
        Path | None,
        typer.Option(
            "--scenarios-dir",
            help="Directory to auto-discover scenarios from when none are given explicitly.",
        ),
    ] = None,
    select: Annotated[
        str | None,
        typer.Option(
            "-k",
            "--select",
            help="Filter by filename substring, optionally with ::step-name to run "
            "just one step (matched against steps and cleanup) instead of the full "
            "sequence, e.g. 'my-scenario::cleanup' or '::cleanup' with an explicit "
            "scenario path.",
        ),
    ] = None,
    fail_fast: Annotated[
        bool,
        typer.Option(
            "-x",
            "--fail-fast",
            help="Stop after the first failing scenario instead of running all of them.",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("-v", "--verbose", help="Enable DEBUG-level logging.")
    ] = False,
    log_level: Annotated[
        str | None,
        typer.Option(
            "--log-level",
            help="Explicit log level (DEBUG/INFO/WARNING/ERROR), overrides the config file.",
        ),
    ] = None,
    refresh_credentials: Annotated[
        bool,
        typer.Option(
            "--refresh-credentials",
            help="Re-fetch Vault-generated credentials instead of reusing the cached file.",
        ),
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Reuse a specific run_id instead of generating a fresh one, so "
            "{{run_id}}-derived identifiers target the same resources - e.g. to "
            "manually re-run a crashed run's cleanup step. Only valid with exactly "
            "one scenario.",
        ),
    ] = None,
) -> None:
    """Run (or preview) ERv2 module integration-test scenario(s), unattended."""
    config = ErvItestConfig()
    raw_output_dir = output_dir or config.output_dir
    effective_output_dir = resolve_relative(raw_output_dir)
    effective_scenarios_dir = resolve_relative(scenarios_dir or config.scenarios_dir)
    effective_level_name = "DEBUG" if verbose else (log_level or config.log_level)
    effective_level = getattr(logging, effective_level_name.upper(), logging.INFO)
    scenario_select, only_step = parse_select(select)

    if not dry_run and not raw_output_dir.is_absolute():
        ensure_gitignore_entry(Path.cwd(), f"{raw_output_dir.as_posix()}/")

    scenario_paths = scenarios or discover_scenarios(
        effective_scenarios_dir, select=scenario_select
    )
    if not scenario_paths:
        configure_logging(log_path=None, level=effective_level)
        logger.error("No scenario files found in %s", effective_scenarios_dir)
        if select and ":" in select and "::" not in select:
            logger.error(
                "Hint: -k/--select uses '::' (not ':') to separate the scenario "
                "filter from a step name, e.g. -k 'name::step'."
            )
        raise typer.Exit(code=EXIT_ERROR)

    if run_id is not None and len(scenario_paths) > 1:
        configure_logging(log_path=None, level=effective_level)
        logger.error(
            "--run-id only makes sense with exactly one scenario, but %s were selected",
            len(scenario_paths),
        )
        raise typer.Exit(code=EXIT_ERROR)

    results: list[tuple[str, bool]] = []
    for scenario_path in scenario_paths:
        try:
            passed = run_scenario(
                scenario_path,
                output_dir=effective_output_dir,
                dry_run=dry_run,
                keep=keep,
                config=config,
                log_level=effective_level,
                refresh_credentials=refresh_credentials,
                run_id_override=run_id,
                only_step=only_step,
            )
        except (ValueError, RuntimeError) as exc:
            # Expected, user-actionable setup failures (missing module/terraform
            # config, Vault unreachable/not logged in) - report cleanly instead of a
            # full traceback, which is only useful for genuine bugs.
            configure_logging(log_path=None, level=effective_level)
            log_final(
                scenario_path.name, passed=False, failed_step="setup", reason=str(exc)
            )
            passed = False
        results.append((scenario_path.name, passed))
        if not passed and fail_fast:
            break

    if len(results) > 1:
        log_summary(results)

    overall_passed = all(ok for _, ok in results)
    raise typer.Exit(code=EXIT_OK if overall_passed else EXIT_ERROR)


if __name__ == "__main__":
    app()
