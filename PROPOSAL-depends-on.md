# Proposal: `depends_on` — cross-module dependencies for erv2-itest scenarios

Status: **Draft for discussion** — no implementation yet.

Motivated by [app-sre/er-aws-vpc-endpoint-service#85](https://github.com/app-sre/er-aws-vpc-endpoint-service/pull/85),
where writing an integration test for `er-aws-vpc-endpoint-service` stalled because the
module needs a pre-existing NLB it does not itself create, and there's no stable one in
the test account. The discussion generalized this to `er-aws-msk-connect` (needs an MSK
cluster) and `er-aws-rds-proxy` (needs an RDS instance) — any module whose Terraform
references a resource it doesn't manage.

## 1. Problem statement

erv2-itest today runs exactly **one** module image per scenario: a `module:` block, a
`base_input`, and a sequence of `docker run` steps against that single image. Some ERv2
modules, however, are consumers, not creators, of the AWS resource they need most:

| Module | Needs (not created by the module) | Currently |
|---|---|---|
| `er-aws-rds-proxy` | An RDS instance (`db_instance_identifier`) + a Secrets Manager secret with its credentials | No integration test exists |
| `er-aws-msk-connect` | An MSK cluster (bootstrap servers, cluster name, VPC) | No integration test exists |
| `er-aws-vpc-endpoint-service` | An NLB tagged `kubernetes.io/service-name=<ns>/<svc>` (discovered via Terraform data source, not passed as input at all) | No integration test exists; author explicitly said "TBH, no idea yet" |

None of these three modules currently has *any* erv2-itest coverage, for the same
underlying reason: you can't test module B in isolation when its Terraform can't do
anything useful without a resource that only module A (or, for the NLB case, OpenShift
itself) creates.

Notably, `er-aws-rds`'s own `integration-tests/basic/scenario.yaml` already anticipates
this need in its description:

> Run this scenario with **--keep** when the instance is needed by another integration
> test. The identifier is defined by the base input.

There's no mechanism today to act on that hint — no way to run RDS's scenario as a setup
step for rds-proxy's scenario, capture what it produced, and feed it in.

## 2. Design: `depends_on`

Add an optional `depends_on` list to the scenario schema. Each entry points at **another
ERv2 module's own existing integration-test scenario** — not a duplicated, hand-rolled
input — runs it first, captures its Terraform outputs, and makes them available to the
scenario under test via template substitution. Cleanup runs in reverse order after the
main scenario's own cleanup.

```yaml
depends_on:
  - name: rds                # local alias, used in {{depends_on.rds.*}}
    module:
      image: quay.io/app-sre/er-aws-rds:latest    # explicit — no fallback/guessing
    scenario: https://github.com/app-sre/er-aws-rds/blob/main/integration-tests/basic/scenario.yaml
    input:                    # optional: shallow-merged into the dependency's base_input.data
      engine_version: "16.14"
```

Why reference the dependency's *own* scenario instead of writing a fresh one locally:

- `er-aws-rds`'s `integration-tests/basic/base_input.json` is already a maintained,
  working example of every required RDS field. Duplicating it in `er-aws-rds-proxy`
  means two copies drifting apart the moment RDS adds a new required field.
- The dependency module's own scenario doubles as living documentation of "how do I
  stand this up for a test."
- It's git-native: pin to a branch, tag, or commit SHA for reproducibility, same as any
  other dependency version pin.

## 3. Concrete examples

### 3a. `er-aws-rds-proxy` depending on `er-aws-rds`

RDS proxy's [`RdsProxyData`](https://github.com/app-sre/er-aws-rds-proxy/blob/main/er_aws_rds_proxy/app_interface_input.py)
requires:

```python
db_instance_identifier: str  # REQUIRED — the RDS instance to attach to
auth: Sequence[Auth]  # REQUIRED — auth[].secret_name for SECRETS auth_scheme
vpc_security_group_ids: list[str]  # REQUIRED
vpc_subnet_ids: list[str]  # REQUIRED
engine_family: str = "POSTGRESQL"
```

`er-aws-rds`'s [`module/outputs.tf`](https://github.com/app-sre/er-aws-rds/blob/main/module/outputs.tf)
produces:

```hcl
output "db_host"     { value = aws_db_instance.this.address }
output "db_port"     { value = aws_db_instance.this.port }
output "db_name"     { value = ... }
output "db_user"     { value = ...; sensitive = true }
output "db_password" { value = ...; sensitive = true }
```

Note `db_instance_identifier` isn't an *output* of RDS at all — it's an **input** both
modules must agree on. That's why the dependency needs `{{run_id}}` shared between it and
the main scenario, not an output reference, for that one field:

```yaml
# er-aws-rds-proxy/integration-tests/basic/scenario.yaml
name: rds-proxy-basic
depends_on:
  - name: rds
    module:
      image: quay.io/app-sre/er-aws-rds:latest
    scenario: https://github.com/app-sre/er-aws-rds/blob/main/integration-tests/basic/scenario.yaml
    # er-aws-rds's own base_input.json already sets identifier: "{{run_id}}" —
    # since run_id is shared, db_instance_identifier below resolves to the same value.

module:
  image: quay.io/app-sre/er-aws-rds-proxy:latest
base_input: integration-tests/basic/base_input.json
steps:
  - name: apply
    input:
      db_instance_identifier: "{{run_id}}"     # matches the RDS dependency's identifier
      vpc_security_group_ids: ["sg-0d44ec58a69cc7d62"]
      vpc_subnet_ids: ["subnet-aaa", "subnet-bbb"]
      auth:
        - auth_scheme: SECRETS
          secret_name: "{{run_id}}-rds-credentials"
    expect:
      exit_code: 0
      log_contains: ["Apply complete!"]
cleanup:
  name: destroy
  action: Destroy
  expect: { exit_code: 0, log_contains: ["Destroy complete!"] }
```

**Open gap**: `auth[].secret_name` must point at an *existing* AWS Secrets Manager
secret containing `db_user`/`db_password` — RDS's Terraform outputs those values but
does **not** create a Secrets Manager secret from them (that normally happens via
app-interface's separate secret-sync machinery, outside either module). This scenario
either needs a small setup step (e.g. a `pre_run` hook, or a `boto3` call in a
`post_run.py`-style helper on the RDS dependency) to publish `{{depends_on.rds.db_user}}`
/ `{{depends_on.rds.db_password}}` into Secrets Manager under a `{{run_id}}`-derived
name — or the proxy scenario accepts `iam_auth: REQUIRED` instead of `SECRETS`, sidestepping
the secret entirely for test purposes. Flagged as an open question in §6.

### 3b. `er-aws-msk-connect` depending on `er-aws-msk`

`MskConnectData` requires:

```python
msk_cluster: str  # cluster name (for IAM policy ARN construction)
kafka_cluster_bootstrap_servers: str  # comma-separated host:port
vpc: VpcConfig  # subnets + security_groups
service_execution_role: str  # IAM role name (separate resource)
custom_plugin: CustomPlugin  # S3 bucket/key of the connector jar
connector_configuration: dict[str, str]
```

`er-aws-msk`'s [`terraform/outputs.tf`](https://github.com/app-sre/er-aws-msk/blob/main/terraform/outputs.tf)
produces exactly the connection string needed:

```hcl
output "bootstrap_brokers_sasl_iam" { value = aws_msk_cluster.this.bootstrap_brokers_sasl_iam }
output "bootstrap_brokers_tls"      { value = aws_msk_cluster.this.bootstrap_brokers_tls }
```

This is the cleanest of the three cases — a direct output-to-input mapping with no
identifier-matching trick required:

```yaml
# er-aws-msk-connect/integration-tests/basic/scenario.yaml
name: msk-connect-basic
depends_on:
  - name: msk
    module:
      image: quay.io/app-sre/er-aws-msk:latest
    scenario: https://github.com/app-sre/er-aws-msk/blob/main/integration-tests/basic/scenario.yaml

module:
  image: quay.io/app-sre/er-aws-msk-connect:latest
base_input: integration-tests/basic/base_input.json
steps:
  - name: apply
    input:
      msk_cluster: "{{run_id}}"
      kafka_cluster_bootstrap_servers: "{{depends_on.msk.bootstrap_brokers_sasl_iam}}"
      vpc:
        subnets: ["subnet-aaa", "subnet-bbb"]
        security_groups: ["sg-111"]
      service_execution_role: "{{run_id}}-msk-connect-role"   # separate IAM role fixture, see below
      custom_plugin:
        s3_bucket_arn: "arn:aws:s3:::erv2-itest-fixtures"
        s3_key: "connectors/debezium-postgres-2.5.0.zip"
        content_type: zip
      connector_configuration:
        connector.class: io.debezium.connector.postgresql.PostgresConnector
    expect:
      exit_code: 0
      log_contains: ["Apply complete!"]
cleanup:
  name: destroy
  action: Destroy
  expect: { exit_code: 0, log_contains: ["Destroy complete!"] }
```

`service_execution_role` and `custom_plugin`'s S3 object are still standing gaps — an IAM
role and an S3 object that neither `er-aws-msk` nor `er-aws-msk-connect` create. These are
plausibly test-account constants (a pre-provisioned role/bucket dedicated to erv2-itest
runs), not additional `depends_on` entries, since they're static fixtures rather than
per-run resources.

### 3c. `er-aws-vpc-endpoint-service` (not an ERv2-module dependency at all)

This is the case that started the discussion, and it doesn't actually fit `depends_on` as
designed above. `VpcEndpointServiceData` takes no NLB reference whatsoever — `main.tf`
discovers it via a Terraform data source:

```hcl
data "aws_lb" "openshift" {
  tags = {
    "kubernetes.io/service-name" = "${var.provision.target_namespace}/${var.openshift_service_name}"
  }
}
```

The NLB is created by the OpenShift cloud controller when a `type: LoadBalancer` Service
is provisioned in-cluster — not by any ERv2 module. So `depends_on: [{scenario: ...}]`
has nothing to point at.

Two options, both **out of scope for this proposal** but recorded here since they were
raised in the same discussion:

1. **Implement `mode: terraform` fixtures** — a `depends_on` entry that runs a plain
   Terraform module (no ERv2 docker wrapper) to create just the tagged NLB. This is the
   same `TerraformRunner` stub already scaffolded in `runner.py`, finally given a job to
   do. Fits naturally as `depends_on[].terraform:` instead of `depends_on[].scenario:`.
2. **A real OpenShift Service in a real test namespace** — creating the NLB isn't a
   Terraform problem at all, it's "apply a k8s Service manifest to a cluster and wait for
   the cloud controller." Out of erv2-itest's current scope (Docker/Terraform-only, no
   kubeconfig handling today).

This proposal recommends (1) as the more consistent long-term direction, but does not
implement it — see §6.

## 4. Schema reference

New top-level scenario field:

```yaml
depends_on:                # optional, list[Dependency], default []
  - name: <str>             # required, unique within the scenario; used as {{depends_on.<name>.*}}
    module:                 # required — ModuleConfig, same shape as the top-level `module:`
      image: <str>
      credentials_file: <path>       # optional, same Vault-or-manual rules as main module
      target_account: <str>
      tf_state_account: <str>
    scenario: <str>          # required — GitHub blob URL or local filesystem path
    input: {}                # optional, dict — shallow-merged into the dependency's base_input.data
```

`scenario` resolution:
- **GitHub URL**: `https://github.com/<owner>/<repo>/blob/<ref>/<path>` — `<ref>` (branch,
  tag, or commit SHA) is parsed from the URL and used to fetch both the scenario YAML and
  its `base_input` path, from the same ref, via the GitHub API (reuses the `gh` CLI's
  existing auth — no new credential handling).
- **Local path**: any other value is treated as a filesystem path (relative to CWD or
  absolute) — for developing against a dependency repo checked out alongside this one.

## 5. Execution semantics

```
1. Generate run_id once, shared by every dependency and the main scenario.
2. For each depends_on entry, in declaration order:
   a. Fetch the referenced scenario + its base_input (GitHub API or local read).
   b. Override module.image per the depends_on entry (always required, never inherited
      from the fetched scenario's own `module:` — the version under test is explicit).
   c. Shallow-merge `input:` into the fetched base_input's `data`.
   d. Substitute {{run_id}} (shared) in the resulting input.
   e. Resolve credentials (Vault or manual, same as today) for the dependency's module.
   f. Run the dependency's own `steps:` sequentially, in its own work_dir
      (<main_work_dir>/depends_on/<name>/), exactly like a normal scenario run.
   g. On any step failure: stop — skip remaining dependencies and the main scenario's
      steps entirely, go straight to dependency cleanup (in reverse, for whichever
      dependencies did succeed).
   h. On success: read <dep_work_dir>/output.json (written by the ERv2 base image after
      every real Apply — see er-base-terraform/entrypoint.sh's $OUTPUTS_FILE), extract
      each key's `.value`, store under depends_on.<name>.
3. Validate every {{depends_on.<name>.<key>}} reference used anywhere in the main
   scenario resolves against the captured outputs — fail fast, before running any main
   step, if a name or key is missing (error message lists available keys).
4. Substitute {{depends_on.*.*}} into the main scenario's base_input and each step's
   input, same mechanism as {{run_id}}.
5. Run the main scenario exactly as today: steps, then cleanup.
6. Run each successfully-applied dependency's own cleanup step, in REVERSE declaration
   order (last-created, first-destroyed) — same LIFO discipline as any dependency graph.
   A dependency cleanup failure is logged as a warning; remaining dependency cleanups
   still run (don't let one stuck resource orphan the rest).
```

Flag interactions:
- `--dry-run`: fetches and lists dependencies (so a broken URL/ref is caught immediately)
  but touches no Docker/AWS/Vault, same guarantee as today.
- `--keep`: skips cleanup for the main scenario **and** every dependency.
- `-k "scenario::step"`: matches against the main scenario's `steps`/`cleanup` only —
  dependency steps are not individually re-runnable through this flag in the MVP.
- `--run-id`: the given ID is used for dependencies too, so re-running cleanup against a
  crashed run tears down dependency resources as well as the main ones.

## 6. Open questions / future work

- **Chaining** (a dependency referencing another dependency's outputs): not supported in
  this proposal. Dependencies run independently, in declaration order. If a real need
  arises (e.g. a 3-layer VPC → subnet → NLB chain), it can be added by letting a later
  `depends_on` entry's `input:` also use `{{depends_on.<earlier-name>.*}}`.
- **`mode: terraform` dependencies** for non-ERv2 prerequisites (the NLB case, §3c) —
  natural fit for the existing `TerraformRunner` stub, not designed here.
- **Sensitive outputs**: `er-aws-rds`'s `db_user`/`db_password` outputs are marked
  `sensitive = true` in Terraform, but `terraform output -json` still includes their
  plaintext values (only `terraform output` without `-json` masks them). If
  `{{depends_on.rds.db_password}}` is substituted into a step's input and that input is
  logged (`logger.debug("Step input: %s", ...)` in `cli.py`), the plaintext would land in
  `.erv2-itests/logs/<run_id>.log`. Needs a redaction strategy — e.g. mark specific output
  keys as sensitive (from the Terraform JSON's own `"sensitive": true` field, which is
  already present per-output) and mask them in all log output, not just this debug line.
- **The Secrets Manager gap** for rds-proxy (§3a) and the IAM-role/S3-object gaps for
  msk-connect (§3b): these are fixtures that are neither "outputs of another ERv2 module"
  nor "part of the module under test." Likely resolved as static, pre-provisioned test
  account constants documented in each consuming module's own `integration-tests/README`,
  rather than something erv2-itest needs to model.
- **Caching fetched remote scenarios**: repeated CI runs re-fetch the same
  `scenario.yaml`/`base_input.json` from GitHub on every invocation. Low priority (GitHub
  API is fast and cheap for single small files) but worth revisiting if this becomes a
  rate-limit concern.

## Feedback wanted

- Does `depends_on` referencing another module's own scenario feel right, versus writing
  a fresh reusable "fixture" input inline?
- Is LIFO dependency cleanup order sufficient, or do real dependency graphs need explicit
  ordering hints beyond declaration order?
- Any objection to leaving the vpc-endpoint-service NLB case (§3c) explicitly unsolved
  here, pending a separate `mode: terraform` proposal?
