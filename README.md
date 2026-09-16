<div align="center">

```
   ╔══════════════════════════════════════════════════════╗
   ║                                                        ║
   ║     🧪  E R V 2 - I T E S T  🐳                        ║
   ║                                                        ║
   ║     unattended integration tests for ERv2 modules     ║
   ║                                                        ║
   ╚══════════════════════════════════════════════════════╝
```

**Run a real ERv2 module image against real AWS resources. Unattended. Safely.**

[![PyPI](https://img.shields.io/pypi/v/erv2-itest)](https://pypi.org/project/erv2-itest/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

</div>

---

## ✨ What is this?

`erv2-itest` runs an [ERv2](https://github.com/app-sre) module's Docker image through a
sequence of `docker run` steps — Apply, Apply again, Destroy — and checks each step's exit
code and log output against what you expect. No manual `docker run` + eyeballing logs, no
guessing whether a reconcile loop actually did the right thing.

- 📄 **YAML scenarios** — describe a sequence of steps, each with an expected exit code and
  optional log-content assertions.
- 🤖 **Fully unattended** — a scenario run's pass/fail is a single process exit code, ready
  for CI or an agent to check without parsing logs.
- 🛡️ **Interrupt-safe** — Ctrl-C or a timeout kills the actual container, not just the local
  CLI client, so a real AWS operation never keeps running unnoticed.
- 🧩 **Module-agnostic** — knows only that a module's input is `data`/`provision`-shaped
  JSON and that it reports success/failure via exit code + stdout. Your scenario YAMLs and
  base-input templates live in your own module's repo, not here.

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
  credentials_file: integration-tests/credentials
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

Preview it (default `--dry-run`, touches nothing):

```bash
uv run erv2-itest integration-tests/my-module-happy-path.yaml
```

Run it for real:

```bash
uv run erv2-itest integration-tests/my-module-happy-path.yaml --no-dry-run
```

Run artifacts (per-run Terraform working dir, full log, JSON summary) land under
`.erv2-itests/` in your current directory.

## 📋 Scenario format

See [`SCENARIO_SCHEMA.md`](SCENARIO_SCHEMA.md) for the full schema reference.

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
