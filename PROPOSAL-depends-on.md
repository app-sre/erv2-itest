# Proposal: `fixtures` — reusable, cross-module setup for erv2-itest scenarios

Status: **Draft for discussion** — no implementation yet.
[SCENARIO_SCHEMA.md](SCENARIO_SCHEMA.md) describes the current behavior.

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

| Module                        | Needs (not created by the module)                                                                                        | Currently                                                             |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| `er-aws-rds-proxy`            | An RDS instance (`db_instance_identifier`) + a Secrets Manager secret with its credentials                               | No integration test exists                                            |
| `er-aws-msk-connect`          | An MSK cluster (bootstrap servers, cluster name, VPC)                                                                    | No integration test exists                                            |
| `er-aws-vpc-endpoint-service` | An NLB tagged `kubernetes.io/service-name=<ns>/<svc>` (discovered via Terraform data source, not passed as input at all) | No integration test exists; author explicitly said "TBH, no idea yet" |

None of these three modules currently has *any* erv2-itest coverage, for the same
underlying reason: you can't test module B in isolation when its Terraform can't do
anything useful without a resource that only module A (or, for the NLB case, OpenShift
itself) creates.

Notably, `er-aws-rds`'s own `integration-tests/basic/scenario.yaml` already anticipates
this need in its description:

> Run this scenario with **--keep** when the instance is needed by another integration
> test. The identifier is defined by the base input.

There's no mechanism today to act on that hint — no way to run RDS's scenario as setup
for rds-proxy's scenario, capture what it produced, and feed it in. And `--keep` alone
leaves input/output wiring and cleanup entirely to the caller: copying the setup into
consumers creates configuration that drifts, and publishing separate, otherwise-unused
fixture bundles carries the same risk.

We need reusable setup that stays exercised by ordinary scenarios, supports input
variations, and can live for one scenario or a complete test session.

## 2. Design: `fixtures`

Model this like [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html):
reusable setup, values passed to consumers, dependencies between fixtures, and teardown
when the fixture's scope ends. Here, a whole scenario is the test — `scenario` scope is
analogous to pytest's `function` scope, while `session` covers one CLI invocation.

Add an optional `fixtures` mapping to the scenario schema. Each named fixture either
**references another module's own existing scenario** — not a duplicated, hand-rolled
input — or **defines setup inline** using the normal scenario fields (`mode`,
`module`/`terraform`, `base_input`, `steps`, `cleanup`). No separate environment manifest
is required.

| Field            | Purpose                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------ |
| `scope`          | `scenario` (default) or `session`.                                                               |
| `scenario`       | Source object for an existing scenario; mutually exclusive with an inline lifecycle.             |
| `mode`           | `erv2` or `terraform`; inherited for references, and must agree if explicitly supplied.          |
| `input`          | Shallow overrides of the fixture's starting `base_input.data`.                                   |
| `step_overrides` | Input overrides by step name, e.g. `step_overrides.<name>.input`.                                |
| `setup_until`    | Optional last setup step, inclusive. Otherwise execute all normal steps.                         |
| `requires`       | Other fixture aliases that must be ready first when no value reference expresses the dependency. |

Referenced scenarios retain their assertions and nested fixtures; their `cleanup` is
deferred until the fixture's scope ends. Inline fixtures use their alias as the default
name. Every fixture requires cleanup; standalone scenarios keep their existing behavior.

Why reference the dependency's *own* scenario instead of writing fresh setup locally,
when a suitable one already exists:

- `er-aws-rds`'s `integration-tests/basic/base_input.json` is already a maintained,
  working example of every required RDS field. Duplicating it in `er-aws-rds-proxy`
  means two copies drifting apart the moment RDS adds a new required field.
- The dependency module's own scenario doubles as living documentation of "how do I
  stand this up for a test."
- It's git-native: pin to a branch, tag, or commit SHA for reproducibility, same as any
  other dependency version pin.

### Sources

Scenario references always use an object, including local files:

```yaml
scenario:
  path: integration-tests/basic/scenario.yaml
```

For another checkout, specify its execution root:

```yaml
scenario:
  root: ../er-aws-rds
  path: integration-tests/basic/scenario.yaml
```

Remote sources add `repo` and `ref`, as in the RDS example below (§3a). Use the same
source objects under `terraform.source`, pointing to a directory containing `.tf` files.
This replaces the GitHub-blob-URL scheme from an earlier draft of this proposal — a
structured object carries the same information without URL parsing, and gives
local/other-checkout/remote sources the same shape.

Paths and project defaults belong to the source's execution root; consumer overrides
belong to the consumer's root. Do not inherit the consumer's module image for an imported
scenario — the version under test is always explicit, never guessed. Resolve remote refs
once per session and retain that revision for cleanup.

### Scope and adaptation

| Scope      | Lifetime                                                                                                  |
| ---------- | --------------------------------------------------------------------------------------------------------- |
| `scenario` | Setup once before the consumer's steps; teardown after its cleanup.                                       |
| `session`  | Share identical effective configurations across scenarios in one CLI invocation; teardown at session end. |

Different input configurations create separate session instances, each with its own run
ID and working directory. A session fixture may depend only on session fixtures; a
scenario fixture may depend on either scope. Nested reuse follows the same rules.

Input precedence is base input, fixture `input`, then each step's input with its named
overrides (`step_overrides`). Shallow merging: nested objects/lists are replaced, not
merged recursively. Cleanup retains its own overrides. Changing a fixture's `input` does
not force that value over every subsequent step, nor mutate an already-shared session
instance.

### Values

| Reference                           | Meaning                                            |
| ----------------------------------- | -------------------------------------------------- |
| `{{run_id}}`                        | The current scenario or fixture instance's own ID. |
| `{{fixtures.rds.run_id}}`           | The RDS fixture's own instance ID.                 |
| `{{fixtures.rds.input.identifier}}` | Its effective input at the setup boundary.         |
| `{{fixtures.rds.outputs.db_host}}`  | An output captured after successful setup.         |

Whole-value references preserve JSON types; interpolation inside strings accepts scalar
values only. References between fixtures establish dependency order — `requires` is only
needed when no such reference exists. Aliases are local to their declaring scenario,
including nested scenarios.

### Terraform variables

A Terraform fixture's effective `input` (base input, if any, merged with the fixture's
own `input` overrides and any `{{...}}` substitutions, above) is written as a single
generated `<fixture work_dir>/generated.auto.tfvars.json`, which Terraform auto-loads —
the same "one file carries the whole input" convention `mode: erv2` already uses for
`/inputs/input.json` (`docker_runner.py`), rather than per-key `-var` flags. Every
top-level key in `input` must match a declared `variable` block in the source's `.tf`
files, and every required variable without a default must have a corresponding key —
checked with `terraform validate` against the generated file before `apply` runs, same
fail-fast-before-touching-AWS principle as reference resolution (§5, step 1).

## 3. Concrete examples

### 3a. `er-aws-rds-proxy` depending on `er-aws-rds`, plus a Secrets Manager fixture

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
modules must agree on. The proxy scenario reads the fixture's actual input
(`{{fixtures.rds.input.identifier}}`) instead of assuming their run IDs match.

`auth[].secret_name` must also point at an *existing* Secrets Manager secret containing
`db_user`/`db_password` — RDS's Terraform outputs those values but does **not** create a
secret from them (that normally happens via app-interface's separate secret-sync
machinery, outside either module). Rather than leave that as an unmodeled gap, a second,
inline `mode: terraform` fixture publishes the secret directly from the RDS fixture's
outputs:

```yaml
# er-aws-rds-proxy/integration-tests/basic/scenario.yaml
name: rds-proxy-basic
mode: erv2
module:
  image: quay.io/app-sre/er-aws-rds-proxy:<tested-tag>
base_input: integration-tests/basic/base_input.json
fixtures:
  rds:
    scope: session
    mode: erv2
    scenario:
      repo: https://github.com/app-sre/er-aws-rds.git
      ref: 6fe438e84bc1da27a496cb25db089a73e7ee00d1
      path: integration-tests/basic/scenario.yaml
    module:
      image: quay.io/app-sre/er-aws-rds:<tested-tag>
    input:
      instance_class: db.t4g.micro
      parameter_group:
        name: "{{run_id}}-pg"
        family: postgres16
        description: Parameter Group for PostgreSQL 16
        parameters:
          - name: rds.logical_replication
            value: 1
            apply_method: pending-reboot
  credentials:
    scope: session
    mode: terraform
    terraform:
      source:
        path: integration-tests/basic/credentials
    input:
      region: us-east-1
      secret_name: "{{run_id}}-rds-credentials"
      username: "{{fixtures.rds.outputs.db_user}}"
      password: "{{fixtures.rds.outputs.db_password}}"
    steps:
      - name: publish credentials
        expect: { exit_code: 0 }
    cleanup:
      name: delete credentials
      action: Destroy
      expect: { exit_code: 0 }
steps:
  - name: apply
    input:
      db_instance_identifier: "{{fixtures.rds.input.identifier}}"
      vpc_security_group_ids: ["sg-0d44ec58a69cc7d62"]
      vpc_subnet_ids: ["subnet-aaa", "subnet-bbb"]
      auth:
        - auth_scheme: SECRETS
          secret_name: "{{fixtures.credentials.outputs.secret_name}}"
    expect:
      exit_code: 0
      log_contains: ["Apply complete!"]
cleanup:
  name: destroy
  action: Destroy
  expect: { exit_code: 0, log_contains: ["Destroy complete!"] }
```

`credentials/main.tf` is a small configuration added to `er-aws-rds-proxy`'s own repo: it
accepts `region`, `secret_name`, `username`, and `password`, creates the Secrets Manager
secret and its version, and exports `secret_name`. Mark credential variables sensitive.
IAM authentication alone does not remove this prerequisite — the proxy
[looks up a secret for each auth entry](https://github.com/app-sre/er-aws-rds-proxy/blob/main/module/main.tf)
regardless.

A second scenario with the same fixture configuration shares the database and secret
(`scope: session`). Changing `instance_class` creates a separate database instance and,
through the `{{fixtures.rds.outputs...}}` reference, a separate secret. Cleanup order is
proxy, then secret, then database — respecting each fixture's own scope.

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
mode: erv2
module:
  image: quay.io/app-sre/er-aws-msk-connect:<tested-tag>
base_input: integration-tests/basic/base_input.json
fixtures:
  msk:
    scope: session
    mode: erv2
    scenario:
      repo: https://github.com/app-sre/er-aws-msk.git
      ref: <tested-commit>
      path: integration-tests/basic/scenario.yaml
    module:
      image: quay.io/app-sre/er-aws-msk:<tested-tag>
steps:
  - name: apply
    input:
      msk_cluster: "{{fixtures.msk.run_id}}"
      kafka_cluster_bootstrap_servers: "{{fixtures.msk.outputs.bootstrap_brokers_sasl_iam}}"
      vpc:
        subnets: ["subnet-aaa", "subnet-bbb"]
        security_groups: ["sg-111"]
      service_execution_role: "{{run_id}}-msk-connect-role"   # static test-account fixture, see below
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
role and an S3 object that neither `er-aws-msk` nor `er-aws-msk-connect` create, and that
don't vary per run the way the RDS-proxy credentials secret does. These are plausibly
test-account constants (a pre-provisioned role/bucket dedicated to erv2-itest runs),
documented in the consuming module's own `integration-tests/README`, rather than
additional fixtures.

### 3c. `er-aws-vpc-endpoint-service`'s NLB, via an inline Terraform fixture

This is the case that started the discussion, and it doesn't fit a scenario-reference
fixture at all: `VpcEndpointServiceData` takes no NLB reference whatsoever — `main.tf`
discovers it via a Terraform data source:

```hcl
data "aws_lb" "openshift" {
  tags = {
    "kubernetes.io/service-name" = "${var.provision.target_namespace}/${var.openshift_service_name}"
  }
}
```

The NLB is normally created by the OpenShift cloud controller when a `type: LoadBalancer`
Service is provisioned in-cluster — not by any ERv2 module, and erv2-itest has no
kubeconfig handling to provision one that way. An earlier draft of this proposal left
this case unsolved, pending a hypothetical future `mode: terraform` fixture. Since
fixtures already support inline `mode: terraform` setup (§2), the same mechanism solves
it directly: a small Terraform config creates a plain NLB carrying the tag the module's
data source looks for, without touching OpenShift at all.

Using [JF's `main.tf`](https://gist.github.com/jfchevrette/01904c3117ce940dc2f509676b2f833f)
in `integration-tests/private-dns/`:

```yaml
name: vpc-endpoint-service-basic
mode: erv2
module:
  image: quay.io/app-sre/er-aws-vpc-endpoint-service:<tested-tag>
base_input: integration-tests/private-dns/base_input.json
fixtures:
  nlb:
    scope: scenario
    mode: terraform
    terraform:
      source:
        path: integration-tests/private-dns
    input:
      run_id: "{{run_id}}"
      target_cluster: appint-ex-01
      target_namespace: example-vpces
    steps:
      - name: create NLB
        expect: { exit_code: 0 }
    cleanup:
      name: destroy NLB
      action: Destroy
      expect: { exit_code: 0 }
steps:
  - name: create endpoint service
    input:
      openshift_service_name: "{{fixtures.nlb.run_id}}"
    expect: { exit_code: 0 }
cleanup:
  name: destroy endpoint service
  action: Destroy
  expect: { exit_code: 0 }
```

The consumer's base input uses `provision.target_namespace: example-vpces`. Both modules
use the same account and region; the cluster must have the subnet tags queried by JF's
HCL. The consumer's service name matches the fixture's run ID, making its existing tag
lookup find the NLB — no NLB output, and no separate ERv2 NLB module, is needed.

## 4. Schema reference

New top-level scenario field:

```yaml
fixtures:                        # optional, map[str, Fixture], default {}
  <alias>:
    scope: scenario | session    # optional, default scenario
    scenario:                    # source object; mutually exclusive with an inline lifecycle
      path: <str>                 #   local file, relative to the execution root
      root: <str>                 #   optional, another checkout's directory
      repo: <str>                 #   optional, remote git URL
      ref: <str>                  #   required with repo — branch, tag, or commit SHA
    mode: erv2 | terraform       # inherited from an existing scenario; must agree if set explicitly
    module: {}                   # ModuleConfig, same shape as the top-level `module:`, for inline erv2 fixtures
    terraform:                    # for inline terraform fixtures
      source: <same source object as `scenario`, pointing at a directory of .tf files>
    base_input: <path>           # for inline fixtures only
    input: {}                    # optional — shallow-merged into the fixture's base_input.data
    step_overrides:               # optional — input overrides by step name
      <step name>:
        input: {}
    setup_until: <step name>      # optional — last setup step to run, inclusive
    requires: [<alias>, ...]      # optional — other fixture aliases that must be ready first
    steps: []                     # for inline fixtures only, same shape as top-level `steps`
    cleanup: {}                   # required for inline fixtures; referenced scenarios keep their own
```

`scenario`/`terraform.source` resolution:

- **Local path**: `path` alone (optionally with `root` for another checkout) is resolved
  relative to CWD or the given root.
- **Remote**: `repo` + `ref` + `path` fetches from git at that revision — reuses the
  existing credential-resolution approach, no new auth mechanism.

## 5. Execution semantics

1. Resolve sources and validate references, cycles, scopes, and named overrides before
   provisioning. Reuse matching session instances; execute prerequisites first (fixtures
   depended on via `requires` or a value reference, then the consumer).
2. Register cleanup before setup starts. Run each fixture's own setup steps and
   assertions, in dependency order. The setup boundary must be a successful real Apply;
   an expected failure or a plan-only step is not readiness.
3. Capture fresh outputs (`/work/output.json` for ERv2; `terraform output -json` for
   Terraform) after each fixture's setup. Reject stale or missing referenced outputs;
   fixtures needing no outputs remain valid. Propagate sensitivity and redact generated
   inputs, logs, and summaries — see the sensitive-outputs question in §6.
4. Run the consumer's own `steps`, then its `cleanup`.
5. Clean up fixtures in reverse dependency order (LIFO — last set up, first torn down),
   at each fixture's own scope. A partial-setup failure runs cleanup for whatever did
   succeed; a cleanup failure is logged as a warning so remaining cleanups still run, and
   the overall invocation still fails. Failed session setup is not silently retried.

Implement the existing Terraform runner for both standalone scenarios and fixtures, using
isolated local state, explicit variables (§2, Terraform variables), and the normal step
timeouts/assertions.

### Cleanup and crash recovery

Raised in review ([discussion](https://github.com/app-sre/erv2-itest/pull/14#discussion_r4126064807)):
this isn't a new guarantee invented for fixtures — it's the same mechanism standalone
scenarios already rely on today, applied uniformly to every fixture instance too.

What already exists, for any scenario, fixture or not:

- **Ordinary step failure**: `run_scenario()` (`cli.py`) always proceeds to the
  scenario's own `cleanup` after a failed step, unless `--keep` was given — this is
  unconditional today and stays unconditional for fixtures.
- **Timeout or Ctrl-C mid-step**: `docker_runner.py` explicitly runs `docker`/`podman
  kill <container_name>` on both a step timeout and `KeyboardInterrupt`, not just
  killing the local CLI client — this is what stops a real AWS-mutating operation from
  continuing unattended after an interrupt (`tests/test_docker_runner.py` is the
  regression coverage for this). The Terraform runner must apply the same discipline:
  interrupting a fixture's `terraform apply` kills the actual `terraform` process, not
  just erv2-itest's own process tree.
- **What that does NOT do**: killing the in-flight process stops it from doing *more*
  damage, but the exception unwinds out of `run_scenario()` before its automatic
  `cleanup` call is reached — interrupted resources are not auto-destroyed. Recovery is
  a deliberate, manual step today (see `AGENTS.md`): re-invoke with
  `--run-id <that run_id> -k "::<cleanup step name>"` against the same, already-known
  run_id, so `{{run_id}}`-derived resource names still resolve to the real orphaned
  resources.

Fixtures extend that same recovery path instead of replacing it:

- Register each fixture's run_id, resolved source revision, and (for Terraform) its
  state file path *before* that fixture's own setup starts (§5, step 2) — the same
  "durable record survives a crash" idea behind today's `.erv2-itests/runs/<run_id>/`
  and `.erv2-itests/logs/<run_id>.json`.
- The proposed `--cleanup-session <id>` is the fixture-aware generalization of today's
  manual `--run-id ... -k '::cleanup'`: it walks the persisted fixture graph for that
  session and destroys each fixture, in reverse dependency order, against its own
  recorded state — without regenerating a fresh plan, and without re-running setup.
- **Terraform-specific consistency**: unlike a single docker step (atomic from
  erv2-itest's point of view — kill it, and nothing partial remains beyond whatever AWS
  itself already committed), `terraform apply` can be interrupted mid-way through
  creating several resources, leaving local state that is neither empty nor complete. A
  fixture's Terraform state must therefore live at a fixed path under its own persisted
  work_dir — never a temp directory cleaned up independently of the resources it
  describes — and both automatic cleanup and `--cleanup-session` recovery must run
  `terraform destroy` against that *exact* state, never a fresh/empty one, so a
  partially-applied fixture's already-created resources are still targeted by the IDs
  Terraform actually recorded, not silently skipped because setup "never finished."

Persist source revisions, instance IDs, bindings, inputs, and state locations to support
`--cleanup-session <id>`; recovery never repeats setup. Protect retained secret-bearing
files and remove them after successful teardown.

Flag interactions:

- `--dry-run`: resolves sources and previews the fixture dependency graph, but touches no
  Docker/Terraform/AWS/Vault — same guarantee as today.
- `--keep`: retains the whole environment — the consumer and every fixture.
- `-k "scenario::step"`: matches the consumer's own `steps`/`cleanup` only; fixture steps
  are not individually re-runnable through this flag in the MVP.
- `--run-id`: shared with every fixture, so re-running cleanup against a crashed run
  tears down fixture resources too. `--run-id ... -k '::cleanup'` remains target-only and
  preserves shared prerequisites.

## 6. Open questions / future work

- **Nested/chained fixtures**: supported here via `requires` and value references between
  fixtures (a later fixture's `input` can use `{{fixtures.<earlier>.*}}`), but multi-layer
  chains (e.g. a 3-layer VPC → subnet → NLB) aren't exercised by any example above —
  worth a dedicated test before relying on it.
- **Sensitive outputs**: `er-aws-rds`'s `db_user`/`db_password` outputs are marked
  `sensitive = true` in Terraform, but `terraform output -json` still includes their
  plaintext values (only `terraform output` without `-json` masks them). If
  `{{fixtures.rds.outputs.db_password}}` is substituted into a step's input and that
  input is logged (`logger.debug("Step input: %s", ...)` in `cli.py`), the plaintext
  would land in `.erv2-itests/logs/<run_id>.log`. Needs a redaction strategy — e.g. mark
  specific output keys as sensitive (from the Terraform JSON's own `"sensitive": true`
  field, already present per-output) and mask them in all log output, not just this
  debug line.
- **Static, non-per-run fixtures**: the IAM-role/S3-object gaps for msk-connect (§3b) are
  neither "outputs of another fixture" nor "part of the module under test," and don't
  vary per run the way the RDS-proxy credentials secret does. Likely resolved as
  pre-provisioned test-account constants documented in each consuming module's own
  `integration-tests/README`, rather than something fixtures need to model.
- **Caching fetched remote scenarios**: repeated CI runs re-fetch the same
  `scenario.yaml`/`base_input.json`/Terraform source from git on every invocation. Low
  priority (a single small fetch is fast and cheap) but worth revisiting if this becomes
  a rate-limit or CI-time concern.
- **Parallel execution and cross-invocation sharing**: out of scope here — fixtures share
  state only within one CLI invocation (`session` scope), not across separate
  `erv2-itest` runs or in parallel with each other.

## Feedback wanted

- Does modeling `fixtures` on pytest's fixture scopes (`scenario`/`session`) feel right,
  versus a flatter `depends_on` list that always runs once per scenario?
- Is `requires` clear enough for expressing ordering when no value reference exists, or
  does it invite fixtures whose dependency isn't otherwise visible in the YAML?
- Any objection to resolving remote sources via a `{repo, ref, path}` object (§2,
  Sources) instead of a GitHub blob URL?
- Does solving the vpc-endpoint-service NLB case (§3c) via an inline `mode: terraform`
  fixture, rather than punting it to a separate proposal, seem like the right scope for
  this one?
