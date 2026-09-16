# erv2-itest

Unattended integration-test framework for ERv2 modules. Defines test scenarios in YAML,
each a sequence of `docker run` steps against a real module image, with per-step
exit-code and log-content assertions.

This is a **standalone, generic** project: it knows nothing about any specific ERv2
module's schema, only that a module's input is `data`/`provision`-shaped JSON and that a
module container communicates success/failure via exit code + stdout. Scenario YAMLs and
base-input templates (which ARE module-specific) live in the *consuming* module's own
repo, added as a dev dependency — not vendored here.

## READ THIS BEFORE RUNNING ANYTHING FOR REAL

**A real scenario run creates and destroys real AWS resources using real credentials.**
`--dry-run` is the default specifically to make that safe-by-default; `--no-dry-run` is
required to actually touch Docker/AWS. Do not casually pass `--no-dry-run`.

**A `docker run` invocation that gets interrupted (Ctrl-C, a killed parent process, a
tool call that gets cut off) can leave the actual container - and whatever real AWS
operation it's running - orphaned and still executing, even though the local process
that started it is gone.** Killing the local `docker`/`podman` CLI client does not stop
the container itself in the daemon for a non-detached `run`. `docker_runner.py`
explicitly runs `docker kill <container_name>` (not just `process.kill()`) on both
timeout and `KeyboardInterrupt` - see `tests/test_docker_runner.py` for the regression
coverage. If you ever see a scenario run get interrupted, **verify with `docker ps` /
`podman ps` that nothing named `<run_id>-*` is still running** before assuming it's safe
to walk away.

## Layout

```text
erv2-itest/
├── pyproject.toml          # deps, hatchling build backend, erv2-itest console script,
│                            # ruff/mypy/coverage config
├── Makefile                 # format, test, build, dev-env, pypi targets
├── Dockerfile               # base -> test -> pypi multi-stage build (Tekton CI)
├── .tekton/                  # PR + push PipelineRun definitions
├── src/erv2_itest/
│   ├── __init__.py
│   ├── __main__.py            # console-script entry point
│   ├── cli.py                 # typer app + orchestration loop
│   ├── models.py               # pydantic models for the scenario YAML schema
│   ├── docker_runner.py         # builds/runs the docker|podman command, streams+captures output
│   ├── logging_utils.py        # shared logger and JSON run-record serialization
│   └── report.py               # log_step/log_final result formatting
├── smoketest/                # fake, throwaway ERv2-module image + scenarios for testing
│   │                          # the RUNNER's own mechanics - no AWS, no cost, seconds not minutes
│   ├── Dockerfile
│   ├── fake_module.sh
│   ├── credentials          # dummy placeholder, not a real credentials file
│   ├── base_input.json
│   ├── scenario_pass.yaml
│   └── scenario_fail.yaml   # deliberately wrong expectation, proves failures are caught
├── SCENARIO_SCHEMA.md        # full scenario YAML schema reference
└── tests/                    # this framework's own test suite (pytest)
```

`.erv2-itests/` (`runs/` for ephemeral per-run Terraform working dirs, `logs/` for
persisted `<run_id>.log` + `<run_id>.json`) is created relative to the **current working
directory the `erv2-itest` command is invoked from** (see `output_dir()` in `cli.py`) -
i.e. in the consuming module's own repo, not wherever this package is installed.

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
interrupts/timeouts actually kill the container, not just the local CLI client.

## Writing a scenario for a module

See [`SCENARIO_SCHEMA.md`](SCENARIO_SCHEMA.md) for the full schema. In short: a base input
JSON template using `{{run_id}}` for every identifying field, and a scenario YAML with a
`module` (image + credentials), a `steps` list, and an optional `cleanup` step. Nothing in
`src/erv2_itest/` needs to change to support a new module - the framework only knows about
`data`/`provision`-shaped JSON and exit codes/log substrings, never any particular
module's schema.
