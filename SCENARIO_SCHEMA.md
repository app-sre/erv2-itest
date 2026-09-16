# Scenario YAML schema

A scenario file describes one end-to-end test of an ERv2 module: which image to run,
what input to give it, and a sequence of `docker run` steps with expected outcomes. The
schema is enforced by the pydantic models in `src/erv2_itest/models.py` - that file is
the source of truth; this document explains it.

## Top-level fields

| Field         | Type                    | Required | Description                                                                                                  |
| ------------- | ----------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| `name`        | string                  | yes      | Scenario name, shown in logs and the JSON summary.                                                           |
| `description` | string                  | no       | Free-text description of what the scenario exercises. Defaults to `""`.                                      |
| `module`      | [`Module`](#module)     | yes      | Which image to run and which credentials to mount.                                                           |
| `base_input`  | path                    | yes      | Path (resolved against the invoking CWD) to a base input JSON template.                                      |
| `steps`       | list of [`Step`](#step) | yes      | The ordered sequence of `docker run` invocations to perform.                                                 |
| `cleanup`     | [`Step`](#step)         | no       | An optional final step (typically `action: Destroy`), run even if a step failed, unless `--keep` was passed. |

Extra/unknown fields anywhere in the schema are rejected (`extra="forbid"`) - a typo in a
field name fails fast instead of being silently ignored.

## `module`

| Field              | Type   | Required | Description                                                                                                        |
| ------------------ | ------ | -------- | ------------------------------------------------------------------------------------------------------------------ |
| `image`            | string | yes      | The module's container image reference.                                                                            |
| `credentials_file` | path   | yes      | Path (resolved against the invoking CWD) to the AWS credentials file mounted into the container at `/credentials`. |

## `step`

Each entry in `steps`, and the optional `cleanup`, has this shape:

| Field             | Type                     | Default   | Description                                                                                                                         |
| ----------------- | ------------------------ | --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `name`            | string                   | -         | Step name, shown in logs and the JSON summary.                                                                                      |
| `input`           | object                   | `{}`      | Merged into the base input's `data` key for this step (see below).                                                                  |
| `action`          | `"Apply"` \| `"Destroy"` | `"Apply"` | Passed to the container as the `ACTION` env var.                                                                                    |
| `dry_run`         | boolean                  | `false`   | Passed to the container as the `DRY_RUN` env var (module-level dry-run, distinct from `erv2-itest`'s own `--dry-run` preview flag). |
| `timeout_seconds` | integer                  | `1800`    | Kill the container if it hasn't exited after this many seconds.                                                                     |
| `expect`          | [`Expectation`](#expect) | -         | What must be true about the step's exit code / log output for it to pass.                                                           |

## `expect`

| Field              | Type           | Default | Description                                                                                |
| ------------------ | -------------- | ------- | ------------------------------------------------------------------------------------------ |
| `exit_code`        | integer        | -       | The container's exit code must equal this.                                                 |
| `log_contains`     | list of string | `[]`    | Every string in this list must appear somewhere in the container's combined stdout/stderr. |
| `log_not_contains` | list of string | `[]`    | None of these strings may appear in the container's output.                                |

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
  credentials_file: integration-tests/credentials
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
