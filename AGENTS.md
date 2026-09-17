# erv2-itest

Agent-facing notes for working *in this repo*. For end-user documentation (install,
quickstart, configuration reference, CLI flags), see [`README.md`](README.md) - that is
the authoritative human-facing doc; this file should never duplicate it, only add
repo-internal context on top.

Unattended integration-test framework for ERv2 modules. Defines test scenarios in YAML,
each a sequence of `docker run` steps against a real module image, with per-step
exit-code and log-content assertions.

This is a **standalone, generic** project: it knows nothing about any specific ERv2
module's schema, only that a module's input is `data`/`provision`-shaped JSON and that a
module container communicates success/failure via exit code + stdout. Scenario YAMLs and
base-input templates (which ARE module-specific) live in the *consuming* module's own
repo, added as a dev dependency — not vendored here.

## READ THIS BEFORE RUNNING ANYTHING FOR REAL

**A real scenario run creates and destroys real AWS resources using real credentials -
and `erv2-itest` does this by default, with no flag needed.** Pass `--dry-run` to only
resolve and print a scenario without touching Docker/AWS/Vault. Do not assume an
invocation is safe just because you didn't pass `--no-dry-run` - it always runs for
real unless `--dry-run` is given explicitly.

**A `docker run` invocation that gets interrupted (Ctrl-C, a killed parent process, a
tool call that gets cut off) can leave the actual container - and whatever real AWS
operation it's running - orphaned and still executing, even though the local process
that started it is gone.** Killing the local `docker`/`podman` CLI client does not stop
the container itself in the daemon for a non-detached `run`. `docker_runner.py`
explicitly runs `docker kill <container_name>` (not just `process.kill()`) on both
timeout and `KeyboardInterrupt` - see `tests/test_docker_runner.py` for the regression
coverage. If you ever see a scenario run get interrupted, **verify with `docker ps` /
`podman ps` that nothing named `<run_id>-*` is still running** before assuming it's safe
to walk away. To manually clean up resources left by an interrupted/crashed run, find
its run_id under `.erv2-itests/runs/` and re-run just the cleanup step against it:
`erv2-itest <scenario>.yaml --run-id <that run_id> -k "::<cleanup step name>"` - see
`find_step()`/`run_id_override` in `cli.py`.

**If a scenario's `module` omits `credentials_file`, erv2-itest fetches real AWS
credentials from Vault and writes them to `.erv2-itests/credentials-cache/`** (0600
permissions, cached indefinitely - see `vault.py`). This never happens during
`--dry-run`. Requires an already-authenticated local `vault login` session.

## Layout

```text
erv2-itest/
├── pyproject.toml          # deps, hatchling build backend, erv2-itest console script,
│                            # ruff/mypy/coverage config
├── Makefile                 # format, test, build, smoketest, demo, dev-env, pypi targets
├── Dockerfile               # base -> test -> pypi multi-stage build (Tekton CI)
├── .tekton/                  # PR + push PipelineRun definitions
├── src/erv2_itest/
│   ├── __init__.py
│   ├── __main__.py            # console-script entry point
│   ├── cli.py                 # typer app + orchestration loop
│   ├── config.py                # .erv2_itest.yml / env var project settings (pydantic-settings)
│   ├── models.py               # pydantic models for the scenario YAML schema
│   ├── runner.py                # pluggable Runner: DockerErv2Runner today, TerraformRunner stub
│   ├── docker_runner.py         # builds/runs the docker|podman command, streams+captures output
│   ├── vault.py                  # generates an AWS credentials file from Vault KVv2 secrets
│   ├── logging_utils.py        # shared logger and JSON run-record serialization
│   └── report.py               # log_step/log_final/log_summary result formatting
├── smoketest/                # fake, throwaway ERv2-module image + scenarios for testing
│   │                          # the RUNNER's own mechanics - no AWS, no cost, seconds not minutes
│   ├── Dockerfile
│   ├── fake_module.sh
│   ├── credentials          # dummy placeholder, not a real credentials file
│   ├── base_input.json
│   ├── scenario_pass.yaml
│   └── scenario_fail.yaml   # deliberately wrong expectation, proves failures are caught
├── demo/                     # separate fake module + scenarios for showing off the
│   │                          # terminal UI to a team (`make demo`) - not used by
│   │                          # `make test`/pytest at all, see demo/README.md
│   ├── Dockerfile
│   ├── fake_module.sh        # adds a SLEEP_SECONDS knob on top of smoketest's module
│   ├── credentials
│   ├── base_input.json
│   └── scenario_*.yaml       # quick pass, slow multi-step, fails-immediately, fails-on-cleanup
├── SCENARIO_SCHEMA.md        # full scenario YAML schema reference
└── tests/                    # this framework's own test suite (pytest)
```

`.erv2-itests/` (`runs/` for ephemeral per-run Terraform working dirs, `logs/` for
persisted `<run_id>.log` + `<run_id>.json`, `credentials-cache/` for Vault-generated AWS
credentials) is created relative to the **current working directory the `erv2-itest`
command is invoked from** (see `output_dir`/`config.py`) - i.e. in the consuming
module's own repo, not wherever this package is installed. erv2-itest automatically adds
this directory to that repo's `.gitignore` (creating one if needed) the first time it
runs for real, so generated credentials never end up committed.

## Configuration

Full field list and CLI flag reference: the "Configuration" section of [`README.md`](README.md).
Implementation: `config.py`'s `ErvItestConfig` (a `pydantic_settings.BaseSettings`) -
CLI flag > `ERV2_ITEST_*` env var > `.erv2_itest.yml` > built-in default. Auto-discovery
(no scenario argument) is `discover_scenarios()` in `cli.py`.

## Development

```bash
make dev-env    # uv sync
make format     # ruff check --fix + ruff format
make test       # ruff check, ruff format --check, mypy, pytest --cov
make build      # build the test container image locally
```

Run `make test` after touching anything under `src/erv2_itest/` - it exists specifically
because bugs in a reconcile loop's exit-code/log signaling have previously slipped through
manual e2e testing and had to be caught by a human noticing something off in raw logs.
`tests/test_smoketest.py` proves the runner correctly distinguishes a passing scenario
from a failing one against a fake throwaway image; `tests/test_docker_runner.py` proves
interrupts/timeouts actually kill the container, not just the local CLI client;
`tests/test_cli_orchestration.py` proves `--dry-run` never touches Docker/AWS/Vault.

## Writing a scenario for a module

See [`SCENARIO_SCHEMA.md`](SCENARIO_SCHEMA.md) for the full schema. In short: a base input
JSON template using `{{run_id}}` for every identifying field, and a scenario YAML with a
`module` (image + optional credentials), a `steps` list, and an optional `cleanup` step.
Nothing in `src/erv2_itest/` needs to change to support a new module - the framework only
knows about `data`/`provision`-shaped JSON and exit codes/log substrings, never any
particular module's schema.
