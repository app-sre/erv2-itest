<div align="center">

```
═════════════════════════════════════════════════════

   🧪  E R V 2 - I T E S T  🐳

   unattended integration tests for ERv2 modules

═════════════════════════════════════════════════════
```

**Run a real ERv2 module image against real AWS resources. Unattended. Safely.**

[![PyPI](https://img.shields.io/pypi/v/erv2-itest)](https://pypi.org/project/erv2-itest/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

</div>

---

## ✨ What is this?

`erv2-itest` is an unattended integration-test runner for [ERv2](https://github.com/app-sre)
modules. Point it at a YAML scenario — or let it auto-discover every scenario in your
repo — and it drives a sequence of `docker run` steps against a real module image
(Apply, Apply again, Destroy), checking each step's exit code and log output against
what you expect. No manual `docker run` + eyeballing logs, no guessing whether a
reconcile loop actually did the right thing, and no credential files to hand-manage —
it fetches AWS credentials from Vault on demand and falls back to sensible defaults
(image name, config, output paths) so a module usually needs little to no setup to get
its first scenario running.

## 🌟 Features

- 📄 **YAML scenarios** — describe a sequence of steps, each with an expected exit code and
  optional log-content assertions.
- 🤖 **Fully unattended** — a scenario run's pass/fail is a single process exit code, ready
  for CI or an agent to check without parsing logs.
- 🔍 **Test-suite style discovery** — run `erv2-itest` with no arguments and it discovers
  and runs every scenario under `integration-tests/`, including subdirectories, with `-k`
  selection and `-x`/`--fail-fast`.
- 🔐 **No credential files lying around** — omit `credentials_file` and erv2-itest
  generates one from Vault on demand, cached locally, never committed.
- 🛡️ **Interrupt-safe** — Ctrl-C or a timeout kills the actual container, not just the local
  CLI client, so a real AWS operation never keeps running unnoticed.
- 🩹 **Crash recovery** — `-k "::step-name"` reruns just one step (e.g. `cleanup`), and
  `--run-id` targets the exact same resources a crashed run created.
- 🧩 **Module-agnostic** — knows only that a module's input is `data`/`provision`-shaped
  JSON and that it reports success/failure via exit code + stdout. Your scenario YAMLs and
  base-input templates live in your own module's repo, not here.
- 🔌 **Pluggable execution backends** — Docker/ERv2 today, with the architecture already
  in place for a native Terraform mode (no ERv2 wrapper) to land without changing how
  scenarios are written.
- 🐳 **Per-module Docker customization** — force `docker` or `podman`, or pass extra
  environment variables into the container, on a per-module basis.
- 📊 **Structured, persisted results** — every real run leaves a full transcript and a
  machine-readable JSON summary behind under `.erv2-itests/logs/`, independent of pass/fail.
- 🙈 **Keeps your `.gitignore` honest** — automatically adds its own output directory the
  first time you run for real, so generated artifacts (including cached credentials)
  never end up committed.

## 🚀 Quickstart

Add it as a dev dependency of your ERv2 module repo:

```bash
uv add --dev erv2-itest
```

Write a scenario (see [`SCENARIO_SCHEMA.md`](SCENARIO_SCHEMA.md) for the full format):

```yaml
name: my-module-happy-path
module:
  image: my-erv2-module:latest
  # no credentials_file - generated from Vault on demand, see Configuration below
base_input: integration-tests/base_input.json
steps:
  - name: apply
    expect:
      exit_code: 0
      log_contains: ["Apply complete!"]
cleanup:
  name: destroy
  action: Destroy
  expect:
    exit_code: 0
```

Every field beyond `name` and `steps` has a sensible fallback, so even this runs for
real by default:

```yaml
name: my-module-happy-path
steps:
  - name: apply
    expect:
      exit_code: 0
```

erv2-itest fills in the rest: image `<current directory name>:prod`, an empty base
input, and Vault-generated credentials (see "Configuration" below).

**erv2-itest runs for real by default** - `--no-dry-run` is implicit. Preview first if
you want to check what it would do without touching anything:

```bash
uv run erv2-itest integration-tests/my-module-happy-path.yaml --dry-run
```

Then run it for real (no flag needed):

```bash
uv run erv2-itest integration-tests/my-module-happy-path.yaml
```

Or drop it under `integration-tests/` (subdirectories are fine) and run every scenario in the repo at once, like a test suite:

```bash
uv run erv2-itest
```

Run artifacts (per-run Terraform working dir, full log, JSON summary, cached Vault
credentials) land under `.erv2-itests/` in your current directory - automatically added
to `.gitignore` the first time you run for real.

## 📋 Scenario format

See [`SCENARIO_SCHEMA.md`](SCENARIO_SCHEMA.md) for the full schema reference.

## ⚙️ Configuration

Settings are resolved in this order - **CLI flag > `ERV2_ITEST_*` env var > `.erv2_itest.yml` > built-in default**:

```yaml
# .erv2_itest.yml, in your repo root
output_dir: .erv2-itests
scenarios_dir: integration-tests
default_mode: erv2
log_level: INFO
target_account: app-sre/creds/terraform/ter-int-dev/config
tf_state_account: app-sre/creds/terraform/ter-int-dev/config
# default_module / default_terraform / default_base_input: let every scenario in this
# repo omit an identical module:/terraform:/base_input: block - see SCENARIO_SCHEMA.md.
```

| Field                | Default                                      | Description                                                                         |
| -------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------- |
| `output_dir`         | `.erv2-itests`                               | Where run logs, JSON summaries, and the Vault credentials cache are written.        |
| `scenarios_dir`      | `integration-tests`                          | Directory auto-discovered (recursively, so subdirectories are fine) when no scenario path is given on the command line. |
| `default_mode`       | `erv2`                                       | Fallback for a scenario that omits its own `mode:` (`erv2` or `terraform`).         |
| `default_module`     | *(none)*                                     | Fallback `module:` block for scenarios that omit one - see `SCENARIO_SCHEMA.md`.    |
| `default_terraform`  | *(none)*                                     | Fallback `terraform:` block for scenarios that omit one.                            |
| `default_base_input` | *(none)*                                     | Fallback `base_input:` path for scenarios that omit one.                            |
| `log_level`          | `INFO`                                       | Base log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`); `-v`/`--verbose` forces `DEBUG`. |
| `target_account`     | `app-sre/creds/terraform/ter-int-dev/config` | Vault KVv2 path used for the `[default]` AWS profile.                               |
| `tf_state_account`   | `app-sre/creds/terraform/ter-int-dev/config` | Vault KVv2 path used for the `[external-resources-state]` AWS profile.              |

Any field can also be set via an `ERV2_ITEST_<FIELD>` environment variable, e.g.
`ERV2_ITEST_OUTPUT_DIR=/tmp/out`.

### CLI flags

| Flag                         | Description                                                                |
| ---------------------------- | -------------------------------------------------------------------------- |
| `--output-dir PATH`          | Override `output_dir` for this run.                                        |
| `--scenarios-dir PATH`       | Override `scenarios_dir` for this run.                                     |
| `-k`, `--select TEXT`        | Filter by filename substring; add `::step-name` to run just one step (matched against `steps` and `cleanup`) instead of the full sequence. |
| `-x`, `--fail-fast`          | Stop after the first failing scenario instead of running all of them.      |
| `-v`, `--verbose`            | Enable `DEBUG`-level logging.                                              |
| `--log-level LEVEL`          | Explicit log level, overriding `log_level`.                                |
| `--refresh-credentials`      | Re-fetch Vault-generated credentials instead of reusing the cached file.   |
| `--run-id TEXT`              | Reuse a specific run_id instead of generating a fresh one. Only valid with exactly one scenario. |
| `--keep`                     | Skip cleanup (`Destroy`) and keep the run's working directory.             |
| `--dry-run` / `--no-dry-run` | Preview only vs. actually run (the default).                               |

Run `erv2-itest --help` for the always-up-to-date list.

### Manually re-running a single step (e.g. cleanup after a crash)

If a run gets interrupted or crashes mid-way, real AWS resources can be left behind
without ever reaching the scenario's `cleanup` step. `-k`/`--select`'s `::step-name`
suffix lets you run just that one step - and `--run-id` lets you target the exact same
resources the crashed run created (its identifier is baked into `base_input` via
`{{run_id}}`, so a fresh random run_id won't find them):

```bash
# find the crashed run's ID - it's the directory name under .erv2-itests/runs/
ls .erv2-itests/runs/

uv run erv2-itest integration-tests/my-scenario.yaml \
  --run-id erv2it-20260916120144-c747 -k "::cleanup"
```

This skips every other step and does not run the scenario's automatic cleanup
afterward - it runs exactly the one named step.

## 🛠️ Development

```bash
make dev-env    # uv sync
make format     # ruff check --fix + ruff format
make test       # ruff check, ruff format --check, mypy, pytest --cov
make build      # build the test container image locally
```

See [`AGENTS.md`](AGENTS.md) for repo layout and the safety notes you should read before
running a scenario for real.

## 📄 License

[Apache License 2.0](LICENSE)
