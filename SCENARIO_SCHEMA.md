# Scenario YAML schema

A scenario file describes one end-to-end test of an ERv2 module (or, in the future, a
plain Terraform module): which image/module to run, what input to give it, and a
sequence of steps with expected outcomes. The schema is enforced by the pydantic models
in `src/erv2_itest/models.py` - that file is the source of truth; this document explains
it. See also [`AGENTS.md`](AGENTS.md) for the `.erv2_itest.yml` project config file,
which can supply defaults for several of the fields below so you don't have to repeat
them in every scenario.

## Top-level fields

| Field         | Type                    | Required | Description                                                                                                  |
| ------------- | ----------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| `name`        | string                  | yes      | Scenario name, shown in logs and the JSON summary.                                                           |
| `description` | string                  | no       | Free-text description of what the scenario exercises. Defaults to `""`.                                      |
| `mode`        | `"erv2"` \| `"terraform"` | no     | Which [`Runner`](#execution-modes-erv2-vs-terraform) executes this scenario. Defaults to `.erv2_itest.yml`'s `default_mode` (itself `"erv2"` by default). |
| `module`      | [`Module`](#module)     | no       | Which image to run and which credentials to mount. Falls back to `.erv2_itest.yml`'s `default_module`, then to a guessed `<current directory name>:prod` for `mode: erv2` - see [`module`](#module) below. |
| `terraform`   | [`Terraform`](#terraform-mode-terraform) | no* | The Terraform module to test. Required for `mode: terraform` unless `.erv2_itest.yml` sets `default_terraform`. |
| `base_input`  | path                    | no       | Path (resolved against the invoking CWD) to a base input JSON template. Falls back to `.erv2_itest.yml`'s `default_base_input`, then to an empty template (`{}`) if neither is set. |
| `steps`       | list of [`Step`](#step) | yes      | The ordered sequence of steps to perform.                                                                    |
| `cleanup`     | [`Step`](#step)         | no       | An optional final step (typically `action: Destroy`), run even if a step failed, unless `--keep` was passed. |

Extra/unknown fields anywhere in the schema are rejected (`extra="forbid"`) - a typo in a
field name fails fast instead of being silently ignored.

## Execution modes: erv2 vs terraform

- **`mode: erv2`** (the default) - runs `docker run` against an ERv2-wrapped module
  image, passing `data`/`provision`-shaped JSON input. This is the only mode actually
  implemented today.
- **`mode: terraform`** - a plain Terraform module, no ERv2 docker wrapper. The schema
  exists so scenarios can be authored and previewed (`--dry-run`) now, but running one
  for real currently reports a clean `RESULT: FAIL` with reason `"Terraform mode is not
  implemented yet"` rather than crashing - see `TerraformRunner` in `runner.py`.

## `module`

| Field               | Type                       | Required | Description                                                                                                        |
| ------------------- | -------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------ |
| `image`             | string                     | yes      | The module's container image reference.                                                                            |
| `credentials_file`  | path                       | no       | Path (resolved against the invoking CWD) to an AWS credentials file mounted into the container at `/credentials`. If omitted, one is **generated from Vault** - see [Credentials](#credentials-manual-file-or-vault-generated) below. |
| `target_account`    | string                     | no       | Vault KVv2 path for the `[default]` AWS profile, overriding `.erv2_itest.yml`'s `target_account`. Only used when `credentials_file` is omitted. |
| `tf_state_account`  | string                     | no       | Vault KVv2 path for the `[external-resources-state]` AWS profile, overriding `.erv2_itest.yml`'s `tf_state_account`. Only used when `credentials_file` is omitted. |
| `container_engine`  | `"docker"` \| `"podman"`   | no       | Force this module to use a specific container engine instead of the default auto-detect (podman if present, else docker). |
| `extra_env`         | map of string to string    | no       | Additional `-e KEY=VALUE` environment variables passed to the container, beyond the built-in `DRY_RUN`/`ACTION`. Defaults to `{}`. |

### Credentials: manual file or Vault-generated

By default (no `credentials_file`), erv2-itest fetches two Vault KVv2 secrets - one at
`target_account`'s path, one at `tf_state_account`'s path (module-level override, else
`.erv2_itest.yml`'s config-level default, which itself defaults to
`app-sre/creds/terraform/ter-int-dev/config`) - and generates an AWS credentials file
with two profiles:

```ini
[default]
aws_access_key_id = <target_account's aws_access_key_id>
aws_secret_access_key = <target_account's aws_secret_access_key>

[external-resources-state]
aws_access_key_id = <tf_state_account's aws_access_key_id>
aws_secret_access_key = <tf_state_account's aws_secret_access_key>
```

This requires an already-authenticated local `vault login` session (erv2-itest shells
out to the `vault` CLI; it does not do its own Vault authentication) and is **skipped
entirely during `--dry-run`** - dry-run never touches Vault, same as it never touches
Docker/AWS. The generated file is cached (0600 permissions) under
`.erv2-itests/credentials-cache/` and reused indefinitely; pass `--refresh-credentials`
to force a re-fetch. If you'd rather not use Vault at all, set `credentials_file`
explicitly and none of this runs.

## `terraform` (mode: terraform)

| Field       | Type | Required | Description                                                    |
| ----------- | ---- | -------- | ---------------------------------------------------------------|
| `source`    | path | yes      | Path to the Terraform module directory.                        |
| `var_file`  | path | no       | Path to a `.tfvars` file.                                       |

## `step`

Each entry in `steps`, and the optional `cleanup`, has this shape:

| Field             | Type                            | Default    | Description                                                                 |
| ----------------- | --------------------------------- | ---------- | ---------------------------------------------------------------------------|
| `name`             | string                            | -          | Step name, shown in logs and the JSON summary.                             |
| `input`            | object                            | `{}`       | Merged into the base input's `data` key for this step (see below).         |
| `action`           | `"Apply"` \| `"Destroy"`          | `"Apply"`  | Passed to the container as the `ACTION` env var.                           |
| `dry_run`          | boolean                           | `false`    | Passed to the container as the `DRY_RUN` env var (module-level dry-run, distinct from `erv2-itest`'s own `--dry-run` preview flag). |
| `timeout_seconds`  | integer                          | `1800`     | Kill the container if it hasn't exited after this many seconds.            |
| `expect`           | [`Expectation`](#expect)         | -          | What must be true about the step's exit code / log output for it to pass.  |

## `expect`

| Field               | Type          | Default | Description                                                             |
| ------------------- | ------------- | ------- | ------------------------------------------------------------------------|
| `exit_code`          | integer       | -       | The container's exit code must equal this.                              |
| `log_contains`       | list of string | `[]`   | Every string in this list must appear somewhere in the container's combined stdout/stderr. |
| `log_not_contains`   | list of string | `[]`   | None of these strings may appear in the container's output.             |

A step is checked in this order: exit code first, then each `log_contains` needle, then
each `log_not_contains` needle. The first failing check becomes the step's failure reason;
the rest are not evaluated.

## `{{run_id}}` substitution

Every scenario run generates a unique `run_id` (e.g. `erv2it-20260916071544-e801`). Before
the first step, `base_input`'s JSON text has every occurrence of the literal string
`{{run_id}}` replaced with that run ID. Use it for every identifying field in your base
input - the replication-group-id-equivalent, `provision.identifier`, `tf_state_key`,
etc. - so a run can never collide with an existing, unrelated resource:

```json
{
  "data": {
    "identifier": "{{run_id}}"
  },
  "provision": {
    "identifier": "{{run_id}}"
  }
}
```

Each step's own `input` is then shallow-merged into the resulting `data` key (`step.input`
keys override same-named keys already in `data`; nothing else in the base input is
touched).

## Complete example

```yaml
name: elasticache-happy-path
description: apply, apply again (no changes), then destroy
module:
  image: quay.io/app-sre/er-aws-elasticache:latest
  # no credentials_file - generated from Vault's default target_account/tf_state_account
base_input: integration-tests/base_input.json
steps:
  - name: apply
    input:
      engine_version: "7.1"
    action: Apply
    dry_run: false
    timeout_seconds: 1800
    expect:
      exit_code: 0
      log_contains:
        - "Apply complete!"
      log_not_contains:
        - "Error:"
  - name: apply again settles
    action: Apply
    expect:
      exit_code: 0
      log_contains:
        - "No changes."
cleanup:
  name: destroy
  action: Destroy
  expect:
    exit_code: 0
    log_contains:
      - "Destroy complete!"
```
