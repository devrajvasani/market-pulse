# MarketPulse — Terraform Architecture

> **Living document.** It describes the Infrastructure-as-Code (Terraform) for MarketPulse and
> **grows one section per stage** as new infrastructure lands. When a stage adds resources, update
> §4–§5 (root + modules) and append a row to §9 (per-stage log).
>
> |                          |                                                                                                                                                                                                                      |
> | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **Scope**          | Everything under[`infra/`](../infra/) — the root config and the reusable modules.                                                                                                                                    |
> | **Current state**  | **Stage 1** — storage foundation + batch-ingest component.                                                                                                                                                    |
> | **Companion docs** | System/data overview:[architecture.md](architecture.md) · IaC conventions: [`.claude/skills/iac-terraform`](../.claude/skills/iac-terraform/) · Setup: [plan/04_INFRASTRUCTURE_SETUP.md](plan/04_INFRASTRUCTURE_SETUP.md) |

## Contents

1. [Mental model — two layers](#1-mental-model--two-layers)
2. [Scope — what Terraform does (and does NOT do)](#2-scope--what-terraform-does-and-does-not-do)
3. [Conventions (naming · tags · providers · state · secrets)](#3-conventions)
4. [Root files — responsibilities](#4-root-files--responsibilities)
5. [Module catalogue + contracts](#5-module-catalogue--contracts)
6. [The wiring — dependency graph](#6-the-wiring--dependency-graph)
7. [Runtime data flow (infra → behaviour)](#7-runtime-data-flow)
8. [The workflow (write → init → plan → apply)](#8-the-workflow)
9. [Per-stage infrastructure log](#9-per-stage-infrastructure-log)
10. [Appendix — directory tree](#appendix--directory-tree)

---

## 1. Mental model — two layers

All Terraform here splits into exactly two kinds of file:

```
infra/
├── main.tf          ┐
├── variables.tf     │   ROOT CONFIG  ("the recipe for THIS environment")
├── storage.tf       │   – picks the modules, names them, wires them together,
├── ingest.tf        │     passes real values. Knows about "marketpulse-dev".
├── outputs.tf       ┘
│
└── modules/         ┐
    ├── kms/         │   REUSABLE MODULES  ("appliance blueprints")
    ├── s3/          │   – each = one logical service. Generic, no hard-coded
    ├── secret/      │     names. Takes inputs (variables.tf), creates resources
    ├── iam_lambda/  │     (main.tf), returns outputs (outputs.tf).
    ├── lambda/      │     Knows nothing about "marketpulse".
    └── eventbridge/ ┘
```

The analogy: a **module is like a function** — generic, reusable, with parameters
(`variables.tf`) and a `return` (`outputs.tf`). The **root** is the script that *calls* those
functions with concrete arguments and feeds one call's return into the next call's argument.

**Why modules?** The `s3` module is written **once** but called **three times** (bronze/silver/gold)
with identical settings. No copy-paste; change the rule once and all three buckets change. Small
single-purpose modules also keep the IAM policy auditable and the blast radius tiny.

---

## 2. Scope — what Terraform does (and does NOT do)

> **Q: Does Terraform only create/update/delete AWS services, or does it also perform operations
> like adding data into an S3 bucket? In this project, is Terraform used to *build* the
> infrastructure, or also for regular *communication* with it?**

Every cloud system has two distinct "planes", and Terraform lives in only one of them:

|             | **Control plane** (manage the *resource*)                                                  | **Data plane** (use the *resource*)                                                                 |
| ----------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Examples    | create a bucket, set its encryption, attach a policy, create the Lambda function, set the schedule | `PutObject` (write data to S3), `GetObject`, query Athena, invoke business logic, read a secret's value |
| Who does it | **Terraform**                                                                                | **Application code** (our Python in the Lambda, via boto3 / awswrangler)                              |

**Terraform only does the control plane.** It manages the lifecycle (create / update / delete) of
**resource definitions** — the "nouns" of the infrastructure — by declaring a **desired state** and
reconciling reality to match it. It does **not** perform runtime operations such as writing market
prices into the bronze bucket.

**In this project, concretely:**

- **Terraform builds:** the KMS key, the 3 S3 buckets, the secret *container*, the IAM role, the
  Lambda *function*, the EventBridge *rule*.
- **Our Lambda code does the data work — at runtime, not Terraform:** fetch prices from CoinGecko →
  read the API key from Secrets Manager → write Parquet into the bronze bucket. That is all
  boto3/awswrangler (the AWS SDK / data-plane APIs). See [§7 Runtime data flow](#7-runtime-data-flow).

**Analogy:** Terraform is the construction crew + electrician — it builds the warehouse, installs the
shelves, locks, and wiring, and hires the robot worker (Lambda) with a time-clock (EventBridge). It
does **not** carry boxes in every hour — the robot does that on its schedule.

**Two nuances that make the boundary exact:**

1. Terraform *can* upload a **file** as a resource — it uploads our **Lambda `.zip`** (`filename` in
   the `lambda` module). But that is **deploying code** (part of provisioning), not moving *business
   data*. Rule of thumb: *declared + stable* → Terraform; *dynamic, high-volume, ever-changing data*
   → application code.
2. The clearest proof in our own design: the **secret value is deliberately NOT set by Terraform**
   (the state trap, see [§3 Conventions](#3-conventions)). Terraform makes the empty container; the
   value is put in out-of-band via Console/CLI — a *data-plane* operation on Secrets Manager.

**Bottom line:** Terraform = create / update / delete AWS infrastructure. Regular communication with
that infrastructure (data in and out) is done by the running application, **never** by Terraform.

---

## 3. Conventions

| Topic                               | Rule                                                                                                                                                                     | Where                                                  |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| **Naming**                    | `{project}-{env}-{service}-{purpose}`, slug `marketpulse` (one word). e.g. `marketpulse-dev-bucket-bronze-1a2b3c4d`.                                               | every resource                                         |
| **Tags**                      | `default_tags` on the provider → every resource auto-tagged `project` / `environment` / `owner` / `managed-by`. Never hand-tag.                               | [main.tf](../infra/main.tf)                               |
| **Providers**                 | `aws ~> 5.0`, `random ~> 3.6`, `archive ~> 2.4`; `terraform >= 1.6`. Versions locked in [.terraform.lock.hcl](../infra/.terraform.lock.hcl) (**committed**).  | [main.tf](../infra/main.tf)                               |
| **State**                     | `*.tfstate` holds resource attributes (and can hold secret values in plaintext) → **gitignored**. It's Terraform's memory of "what is actually built".          | [.gitignore](../.gitignore)                               |
| **Secrets — the state trap** | Create the secret**container** in TF; set the **value out-of-band** (Console/CLI) so it never enters state. Never put a secret value in `.tf`/`.tfvars`. | [modules/secret](../infra/modules/secret/main.tf)         |
| **IAM**                       | One role per component; only the specific actions on the specific ARNs.**No wildcards**, no `Resource: "*"`, no broad managed policies.                          | [modules/iam_lambda](../infra/modules/iam_lambda/main.tf) |
| **Encryption / exposure**     | KMS (CMK) on buckets + secret;**block all public access**; versioning on.                                                                                          | [modules/s3](../infra/modules/s3/main.tf)                 |
| **Apply authority**           | I write + run `terraform plan` (read-only). **Only the user runs `apply`/`destroy`.** Locally, `tflocal apply` runs against LocalStack for free.           | CLAUDE.md hard rule                                    |

---

## 4. Root files — responsibilities

| File                               | Role                                                                                                                                                                                                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [main.tf](../infra/main.tf)           | **Providers + global tags.** Declares `aws`/`random`/`archive`, pins versions, sets `default_tags`. **No resources.**                                                                                                              |
| [variables.tf](../infra/variables.tf) | **The knobs:** `region` (`us-east-1`), `environment` (`dev`), `owner` (required), `awswrangler_layer_arn` (`""`), `ingest_schedule` (`rate(1 hour)`). Real values come from `terraform.tfvars` (gitignored) or `TF_VAR_*`. |
| [storage.tf](../infra/storage.tf)     | **Storage foundation:** `random_id` suffix → `module.kms` → `module.s3` (×3 via `for_each`).                                                                                                                                          |
| [ingest.tf](../infra/ingest.tf)       | **Batch-ingest component:** `module.secret` → `module.iam_ingest` → `archive_file` → `module.lambda_ingest` → `module.eventbridge_ingest`.                                                                                         |
| [outputs.tf](../infra/outputs.tf)     | **Post-apply readout:** `bucket_names`, `kms_key_arn`, `secret_arn`, `ingest_role_arn`, `ingest_function_name`, `ingest_schedule_rule`.                                                                                              |

---

## 5. Module catalogue + contracts

Six modules today. Each row below is the module's **contract**: what you pass in (inputs), what it
builds (resources), what it hands back (outputs).

### 5.1 Catalogue (one-line responsibility)

| Module                                           | Single responsibility                                                                      | Resources                                                                                               |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| [kms](../infra/modules/kms/main.tf)                 | One CMK (auto-rotated, 7-day delete window) encrypting the whole lake + the secret.        | `aws_kms_key`, `aws_kms_alias`                                                                      |
| [s3](../infra/modules/s3/main.tf)                   | One private, SSE-KMS-encrypted, versioned bucket. Reused per layer.                        | `aws_s3_bucket` + `public_access_block` + `server_side_encryption_configuration` + `versioning` |
| [secret](../infra/modules/secret/main.tf)           | The secret**container** only (KMS-encrypted, 7-day recovery). Value set out-of-band. | `aws_secretsmanager_secret`                                                                           |
| [iam_lambda](../infra/modules/iam_lambda/main.tf)   | The ingest Lambda's least-privilege identity (trust + inline policy, no wildcards).        | `aws_iam_role` + `aws_iam_role_policy`                                                              |
| [lambda](../infra/modules/lambda/main.tf)           | The function itself + its log group (pre-created so retention is finite).                  | `aws_lambda_function` + `aws_cloudwatch_log_group`                                                  |
| [eventbridge](../infra/modules/eventbridge/main.tf) | The schedule + permission for EventBridge to invoke the Lambda.                            | `aws_cloudwatch_event_rule` + `aws_cloudwatch_event_target` + `aws_lambda_permission`             |

### 5.2 `kms`

| Inputs                                     | Outputs                                     |
| ------------------------------------------ | ------------------------------------------- |
| `name` — full key name (also the alias) | `key_arn` · `key_id` · `alias_name` |

### 5.3 `s3`

| Inputs                                                                        | Outputs                         |
| ----------------------------------------------------------------------------- | ------------------------------- |
| `bucket_name` — globally-unique name · `kms_key_arn` — CMK for SSE-KMS | `bucket_id` · `bucket_arn` |

### 5.4 `secret`

| Inputs                                                          | Outputs                           |
| --------------------------------------------------------------- | --------------------------------- |
| `name` · `description` (default `""`) · `kms_key_arn` | `secret_arn` · `secret_name` |

### 5.5 `iam_lambda`

| Inputs                                                                                      | Outputs                       |
| ------------------------------------------------------------------------------------------- | ----------------------------- |
| `name` · `function_name` · `bronze_bucket_arn` · `kms_key_arn` · `secret_arn` | `role_arn` · `role_name` |

The inline policy has exactly **5 scoped statements**: `WriteBronzeObjects` (`s3:PutObject`/`DeleteObject`
on `bronze/*`), `ListBronzeBucket` (`s3:ListBucket` on the bucket), `UseCmkForS3`
(`kms:GenerateDataKey`/`Encrypt`/`DescribeKey` on the CMK), `ReadApiKeySecret`
(`secretsmanager:GetSecretValue` on the one secret), `WriteOwnLogs` (`logs:*` scoped to the
function's own log group). No wildcards.

### 5.6 `lambda`

| Inputs                                                                                                                                                                                                                                                                                    | Outputs                               |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `function_name` · `role_arn` · `source_zip` · `source_hash` · `environment` (map) · `layers` (list) · **defaults:** `handler=src.ingestion.batch.handler.handler`, `runtime=python3.12`, `timeout_seconds=60`, `memory_mb=256`, `log_retention_days=14` | `function_arn` · `function_name` |

`source_hash` (base64-sha256 of the zip) is how Terraform knows the code changed → it redeploys only
when the package actually differs.

### 5.7 `eventbridge`

| Inputs                                                                                 | Outputs                       |
| -------------------------------------------------------------------------------------- | ----------------------------- |
| `name` · `schedule_expression` · `target_lambda_arn` · `target_lambda_name` | `rule_arn` · `rule_name` |

---

## 6. The wiring — dependency graph

You never write "create KMS *before* S3." When the `s3` call references `module.kms.key_arn`, that
reference **is** the ordering instruction. Terraform reads every reference, builds a DAG, and derives
the order + what can run in parallel. Our Stage-1 graph (arrow = "output of A feeds input of B", so A
is built before B):

```
 ┌── TIER 0 · no dependencies ─────────────────────────────────────────────────┐
 │   random_id.bucket_suffix                 module.kms                        │
 │        (.hex)                          (CMK + alias)                        │
 └────────┬───────────────────────┬───────────┬──────────────┬─────────────────┘
          │ hex          key_arn  │   key_arn │     key_arn  │
          ▼                       ▼           ▼              │
 ┌── TIER 1 ───────────────┐  ┌─────────────────────┐        │
 │  module.s3  (×3)        │  │   module.secret     │        │
 │  bronze / silver / gold │  │   (container only)  │        │
 └───┬───────────────┬─────┘  └──────────┬──────────┘        │
     │ bronze        │ bronze            │ secret_arn        │ key_arn
     │ _bucket_arn   │ _bucket_id        │ secret_name       │
     ▼               │                   ▼                   ▼
 ┌── TIER 2 ─────────┼───────────────────────────────────────────────────┐
 │   module.iam_ingest                                                   │
 │   role: write bronze · use CMK · read secret · write own logs         │
 └───────────────────────────────┬───────────────────────────────────────┘
                                  │ role_arn
   data.archive_file.ingest ──────┤ (zip + base64sha256)
   (zips infra/build/lambda)      ▼
 ┌── TIER 3 ──────────────────────────────────────────────────────────────┐
 │   module.lambda_ingest                                                 │
 │   env: BRONZE_BUCKET=bronze_id · MARKETDATA_SECRET_NAME=secret_name    │
 │   layers: var.awswrangler_layer_arn                                    │
 └───────────────────────────────┬────────────────────────────────────────┘
                                  │ function_arn · function_name
                                  ▼
 ┌── TIER 4 ──────────────────────────────────────────────────────────────┐
 │   module.eventbridge_ingest     rate(1 hour) ─► invoke the Lambda      │
 └────────────────────────────────────────────────────────────────────────┘
```

Notes: `module.kms.key_arn` fans out to **three** consumers (s3, secret, iam). The bronze bucket's
`bucket_id` and the secret's `secret_name` also feed the **Lambda's env vars** (Tier 3), not just the
IAM role. KMS is the root of trust; EventBridge is the leaf.

---

## 7. Runtime data flow

The build order above mirrors what happens every hour once deployed — each IAM statement exists
*because* of one arrow here (this is the **data plane** from [§2](#2-scope--what-terraform-does-and-does-not-do),
done by the Lambda, not Terraform):

```
  EventBridge rule (rate 1 hour)
        │ invokes
        ▼
   Lambda  ──assumes──►  IAM role (least-privilege)
        │ reads env: BRONZE_BUCKET, MARKETDATA_SECRET_NAME
        │
        ├─ GetSecretValue ─► Secrets Manager ─(decrypt)─► KMS CMK
        ├─ HTTPS GET ──────► CoinGecko API
        └─ PutObject ──────► s3://marketpulse-dev-bucket-bronze-XXXX/
                                 prices/dt=YYYY-MM-DD/*.parquet
                                      │ encrypted at rest (SSE-KMS)
                                      ▼
                                    KMS CMK
```

---

## 8. The workflow

```
  write .tf ──► terraform init ──► terraform fmt / validate ──► terraform plan ──► apply
  (root+mods)   • fetch providers   • format + static check     • dry-run diff      • YOU (cloud)
                • link ./modules     • (no AWS calls)            • read-only         • tflocal (local)
                • write lock file                                • I run this        • writes state
```

- **`init`** — one-time per machine / after adding a module: fetches providers, links `./modules/*`,
  writes the lock file. *Re-run after adding any module.*
- **`fmt` / `validate`** — formatting + "is this config internally valid?" (no AWS calls). What CI and
  I run on every change. Stage 1 currently reports **"the configuration is valid."**
- **`plan`** — the dry-run diff: compares *your code* (desired) vs *state* (last-known real) and prints
  `+ / - / ~`. Read-only — **I run this**.
- **`apply`** — the only step that mutates real infra. **Only the user runs it** on AWS (after
  cost-reviewer + infra-reviewer). Locally `tflocal apply` targets LocalStack for free.

### 8.1 Commands — create & update (copy-paste)

The **same three commands create *and* update** infrastructure — Terraform diffs your `.tf` against
state and does whatever is needed. "Updation" is simply: **edit a `.tf`, then `plan` → `apply` again.**
All commands run from `infra/`.

**Local — LocalStack (free, safe sandbox) via `tflocal`:**
```powershell
# prereq: Docker running + LocalStack up
docker compose -f infrastructure/localstack/docker-compose.yml up -d
bash scripts/wait_for_localstack.sh

cd infra
uv run tflocal init        # first time / after adding a module
uv run tflocal plan        # preview the diff (read-only)
uv run tflocal apply       # type: yes   (create AND update use this)
uv run tflocal output      # bucket names, ARNs, etc.
uv run tflocal destroy     # tear the sandbox down (free)
```

**Real AWS via `terraform` — `apply`/`destroy` are HUMAN-run (Section N / CLAUDE.md):**
```powershell
# prereq: AWS creds in ~/.aws, awswrangler_layer_arn set in terraform.tfvars,
#         cost-reviewer + infra-reviewer passed
cd infra
terraform init
terraform plan             # ALWAYS review first (Claude may run this — read-only)
terraform apply            # YOU run this — type: yes
terraform output
terraform destroy          # YOU run this before the credits expire (Section I)
```

**Run the ingestion against the local stack** (writes Parquet → bronze):
```powershell
$env:BRONZE_BUCKET = (uv run tflocal output -json bucket_names | ConvertFrom-Json).bronze
$env:APP_ENV = "local"
cd ..
uv run python -m src.ingestion.batch.ingest      # or: make run-local
```

### 8.2 Reading a plan — create vs update vs replace

Same workflow, but the **plan summary** tells you what kind of change it is — always read it before approving:

| Plan summary | Meaning | Symbol |
|---|---|---|
| `N to add, 0 to change, 0 to destroy` | **create** new resources | `+` |
| `0 to add, M to change, 0 to destroy` | **update in place** (safe) | `~` |
| `… to add, … to destroy` (same resource) | **replace** = destroy then recreate — can lose data | `-/+` |

A `~` is a safe in-place edit; a `-/+` replacement on a **stateful** resource (bucket, secret) is
destructive and needs a second look. **Makefile shortcuts:** `make localstack-up` ·
`make wait-localstack` · `make tf-plan` · `make run-local` · `make deploy` / `make destroy`
(the last two *print* the human-run AWS commands rather than running them).

---

## 9. Per-stage infrastructure log

What each stage adds to `infra/`. Append a row when a stage lands; keep §4–§5 in sync.

| Stage        | Theme                                                    | Root files                                    | Modules / resources added                                                                               | Status                                             |
| ------------ | -------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| **0**  | Foundation                                               | `main.tf`, `variables.tf`, `outputs.tf` | Provider +`default_tags` skeleton — **no resources**                                           | ✅ done                                            |
| **1**  | Storage + batch ingest (Bronze)                          | `storage.tf`, `ingest.tf`                 | `kms`, `s3` (×3: bronze/silver/gold), `secret`, `iam_lambda`, `lambda`, `eventbridge`      | ✅ built · validated · local/cloud apply pending |
| **2+** | Transforms · streaming · RAG · orchestration · CI/CD | _tbd_                                       | _Not yet built — appended when the stage lands (e.g. Glue/Athena/Iceberg, Kinesis, Step Functions)._ | ⏳ planned                                         |

### Resource names created in Stage 1 (`environment = dev`)

- KMS key + `alias/marketpulse-dev-kms-lake`
- `marketpulse-dev-bucket-bronze-<hex>` · `…-silver-<hex>` · `…-gold-<hex>`
- `marketpulse-dev-secret-marketdata-apikey`
- `marketpulse-dev-role-lambda-ingest` (+ inline `…-policy`)
- `marketpulse-dev-lambda-batch-ingest` (+ log group `/aws/lambda/marketpulse-dev-lambda-batch-ingest`)
- `marketpulse-dev-rule-ingest-schedule`

---

## Appendix — directory tree

```
infra/
├── main.tf                  provider + default_tags + required_providers
├── variables.tf             region · environment · owner · awswrangler_layer_arn · ingest_schedule
├── storage.tf               random_id + module.kms + module.s3 (×3)
├── ingest.tf                module.secret + iam_ingest + archive_file + lambda_ingest + eventbridge_ingest
├── outputs.tf               bucket_names · kms_key_arn · secret_arn · role/function/rule
├── .terraform.lock.hcl      provider version locks (committed)
└── modules/
    ├── kms/                 CMK + alias
    ├── s3/                  private + SSE-KMS + versioned bucket
    ├── secret/              Secrets Manager container (value out-of-band)
    ├── iam_lambda/          least-privilege role + inline policy
    ├── lambda/              function + log group
    └── eventbridge/         schedule rule + target + invoke permission
```

Each module folder is `main.tf` (resources) + `variables.tf` (inputs) + `outputs.tf` (returns).
