# MarketPulse — Terraform Architecture

> **Living document.** It describes the Infrastructure-as-Code (Terraform) for MarketPulse and
> **grows one section per stage** as new infrastructure lands. When a stage adds resources, update
> §4–§5 (root + modules) and append a row to §9 (per-stage log).
>
> |                          |                                                                                                                                                                                                                      |
> | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **Scope**          | Everything under[`infra/`](../infra/) — the root config and the reusable modules.                                                                                                                                    |
> | **Current state**  | **Stage 4 — streaming added** — Stages 0–3 (storage + batch-ingest + Glue catalog & Athena + **Silver/Gold Iceberg** via dbt) **plus a real-time path**: **Kinesis → consumer Lambda → Bronze NDJSON → dbt `silver_trades`/`gold_latest_price`**, with an SQS **DLQ**. All streaming infra is gated by `enable_streaming` (default off); **Kinesis is account-blocked on this Free Plan** (validated end-to-end on LocalStack). The optional **Glue Spark** job stays gated off (also account-blocked). Catalog gated off for LocalStack (DuckDB twin).                                                                                                                                                    |
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
| **Encryption / exposure**     | KMS (CMK) on buckets + secret;**block all public access**; versioning + lifecycle expiry on.                                                                                          | [modules/s3](../infra/modules/s3/main.tf)                 |
| **Apply authority**           | I write + run `terraform plan` (read-only). **Only the user runs `apply`/`destroy`.** Locally, `tflocal apply` runs against LocalStack for free.           | CLAUDE.md hard rule                                    |

---

## 4. Root files — responsibilities

| File                               | Role                                                                                                                                                                                                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [main.tf](../infra/main.tf)           | **Providers + global tags.** Declares `aws`/`random`/`archive`, pins versions, sets `default_tags`. **No resources.**                                                                                                              |
| [variables.tf](../infra/variables.tf) | **The knobs:** `region` (`us-east-1`), `environment` (`dev`), `owner` (required), `awswrangler_layer_arn` (`""`), `ingest_schedule` (`rate(1 hour)`), `enable_catalog` (`true`; `false` skips Glue/Athena for LocalStack), `enable_glue_spark` (`false`; opt-in Stage-3d Glue Spark job), `enable_streaming` (`false`; opt-in Stage-4 Kinesis/consumer/DLQ streaming stack), `enable_rag` (`false`; opt-in Stage-5 RAG Lambdas — requires `enable_catalog`), `bedrock_embed_model`/`bedrock_gen_model` (scope the RAG Bedrock ARNs). Real values come from `terraform.tfvars` (gitignored) or `TF_VAR_*`. |
| [storage.tf](../infra/storage.tf)     | **Storage foundation:** `random_id` suffix → `module.kms` → `module.s3` (×3 via `for_each`).                                                                                                                                          |
| [ingest.tf](../infra/ingest.tf)       | **Batch-ingest component:** `module.secret` → `module.iam_ingest` → `archive_file` → `module.lambda_ingest` → `module.eventbridge_ingest`.                                                                                         |
| [catalog.tf](../infra/catalog.tf)     | **(Stage 2) Catalog + query:** `module.athena_results` + `module.glue_catalog` + `module.athena` — all gated by `enable_catalog`.                                                                                       |
| [transform.tf](../infra/transform.tf) | **(Stage 3d) Glue Spark learning job:** inline `aws_iam_role` + `aws_iam_role_policy` + `aws_s3_object` (script) + `aws_glue_job` — gated by `enable_glue_spark` (default `false`). *Not* a module (one-off learning resource). Account-blocked on this Free Plan. |
| [streaming.tf](../infra/streaming.tf) | **(Stage 4) Real-time trades:** inline `aws_kinesis_stream` + `aws_sqs_queue` (DLQ) + `aws_iam_role`/`_policy` + `module.lambda_trades_consumer` (reuses the `lambda` module) + `aws_lambda_event_source_mapping` + `aws_glue_catalog_table.bronze_trades` — all gated by `enable_streaming` (default `false`); the table also needs `enable_catalog`. Account-blocked on this Free Plan (Kinesis). |
| [rag.tf](../infra/rag.tf) | **(Stage 5) RAG assistant:** `archive_file` + three least-privilege `aws_iam_role`/`_policy` + three `module.lambda_rag_*` (ingest / index / assistant, reuse the `lambda` module) — all gated by `enable_rag` (default `false`, requires `enable_catalog`). Assistant reads `gold/rag/<env>/`, queries Gold via Athena/Glue (read-only), invokes scoped Bedrock model ARNs. **AWS-deferred:** runs locally via CLI/API (Ollama+DuckDB); a real deploy also needs a faiss/numpy Lambda layer or container. |
| [outputs.tf](../infra/outputs.tf)     | **Post-apply readout:** `bucket_names`, `kms_key_arn`, `secret_arn`, `ingest_role_arn`, `ingest_function_name`, `ingest_schedule_rule`; **+ Stage 2:** `glue_database`, `athena_workgroup`, `athena_results_bucket` (null when catalog off); **+ Stage 3:** `glue_bronze_to_silver_job` (null unless `enable_glue_spark`); **+ Stage 4:** `trades_stream_name`, `trades_dlq_url` (null unless `enable_streaming`); **+ Stage 5:** `rag_assistant_function_name` (null unless `enable_rag`, defined in `rag.tf`).                                                                                              |

---

## 5. Module catalogue + contracts

Eight modules today (Stage 2 added **`glue_catalog`** + **`athena`**). Each row below is the module's **contract**: what you pass in (inputs), what it
builds (resources), what it hands back (outputs).

### 5.1 Catalogue (one-line responsibility)

| Module                                           | Single responsibility                                                                      | Resources                                                                                               |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| [kms](../infra/modules/kms/main.tf)                 | One CMK (auto-rotated, 7-day delete window) encrypting the whole lake + the secret.        | `aws_kms_key`, `aws_kms_alias`                                                                      |
| [s3](../infra/modules/s3/main.tf)                   | One private, SSE-KMS-encrypted, versioned bucket with lifecycle expiry of old versions. Reused per layer.                        | `aws_s3_bucket` + `public_access_block` + `server_side_encryption_configuration` + `versioning` + `lifecycle_configuration` |
| [secret](../infra/modules/secret/main.tf)           | The secret**container** only (KMS-encrypted, 7-day recovery). Value set out-of-band. | `aws_secretsmanager_secret`                                                                           |
| [iam_lambda](../infra/modules/iam_lambda/main.tf)   | The ingest Lambda's least-privilege identity (trust + inline policy, no wildcards).        | `aws_iam_role` + `aws_iam_role_policy`                                                              |
| [lambda](../infra/modules/lambda/main.tf)           | The function itself + its log group (pre-created so retention is finite).                  | `aws_lambda_function` + `aws_cloudwatch_log_group`                                                  |
| [eventbridge](../infra/modules/eventbridge/main.tf) | The schedule + permission for EventBridge to invoke the Lambda.                            | `aws_cloudwatch_event_rule` + `aws_cloudwatch_event_target` + `aws_lambda_permission`             |
| [glue_catalog](../infra/modules/glue_catalog/main.tf) *(Stage 2)* | Glue DB + the `bronze_prices` metadata table (partition projection, no crawler).        | `aws_glue_catalog_database` + `aws_glue_catalog_table`                                            |
| [athena](../infra/modules/athena/main.tf) *(Stage 2)* | Athena workgroup with a 100 MB per-query scan cap + SSE-KMS results.                     | `aws_athena_workgroup`                                                                              |

### 5.2 `kms`

| Inputs                                     | Outputs                                     |
| ------------------------------------------ | ------------------------------------------- |
| `name` — full key name (also the alias) | `key_arn` · `key_id` · `alias_name` |

### 5.3 `s3`

| Inputs                                                                        | Outputs                         |
| ----------------------------------------------------------------------------- | ------------------------------- |
| `bucket_name` — globally-unique name · `kms_key_arn` — CMK for SSE-KMS · `force_destroy` (default `false`; `true` on lake buckets for clean teardown) · `noncurrent_version_expiration_days` / `abort_incomplete_multipart_upload_days` (both default `7`) | `bucket_id` · `bucket_arn` |

Five resources: bucket + public-access-block + SSE-KMS + versioning + **lifecycle** (expire _noncurrent_ versions & abort incomplete multipart uploads after 7 days). Current object versions are never expired — only superseded history and orphaned upload parts, so version growth from more coins / idempotent re-writes can't accrue cost.

### 5.4 `secret`

| Inputs                                                          | Outputs                           |
| --------------------------------------------------------------- | --------------------------------- |
| `name` · `description` (default `""`) · `kms_key_arn` | `secret_arn` · `secret_name` |

### 5.5 `iam_lambda`

| Inputs                                                                                      | Outputs                       |
| ------------------------------------------------------------------------------------------- | ----------------------------- |
| `name` · `function_name` · `bronze_bucket_arn` · `kms_key_arn` · `secret_arn` | `role_arn` · `role_name` |

The inline policy has exactly **5 scoped statements**: `ReadWriteBronzeObjects` (`s3:PutObject`/`GetObject`/`DeleteObject`
on `bronze/*`), `ListBronzeBucket` (`s3:ListBucket`/`GetBucketLocation` on the bucket), `UseCmkForLakeIO`
(`kms:GenerateDataKey`/`Encrypt`/`Decrypt`/`DescribeKey` on the CMK), `ReadApiKeySecret`
(`secretsmanager:GetSecretValue` on the one secret), `WriteOwnLogs` (`logs:*` scoped to the
function's own log group). No wildcards. The `GetObject`/`GetBucketLocation` reads and `kms:Decrypt`
are required by awswrangler and to read the KMS-encrypted secret on real AWS.

### 5.6 `lambda`

| Inputs                                                                                                                                                                                                                                                                                    | Outputs                               |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `function_name` · `role_arn` · `source_zip` · `source_hash` · `environment` (map) · `layers` (list) · **defaults:** `handler=src.ingestion.batch.handler.handler`, `runtime=python3.12`, `timeout_seconds=120`, `memory_mb=256`, `log_retention_days=14` | `function_arn` · `function_name` |

`source_hash` (base64-sha256 of the zip) is how Terraform knows the code changed → it redeploys only
when the package actually differs.

### 5.7 `eventbridge`

| Inputs                                                                                 | Outputs                       |
| -------------------------------------------------------------------------------------- | ----------------------------- |
| `name` · `schedule_expression` · `target_lambda_arn` · `target_lambda_name` | `rule_arn` · `rule_name` |

### 5.8 `glue_catalog` *(Stage 2)*

| Inputs | Outputs |
| ------ | ------- |
| `database_name` · `table_name` · `data_location` (s3 prefix) · `columns` (list of `{name,type}`) · `projection_date_start` | `database_name` · `table_name` |

Metadata only (schema-on-read) — points at the Bronze Parquet and stores zero rows. **Partition projection** computes `snapshot_date`/`snapshot_hour` from the object path → **no crawler ($0), zero-maintenance**. Column types must match the Parquet pinned in `ingest.py` `_BRONZE_DTYPES`.

### 5.9 `athena` *(Stage 2)*

| Inputs | Outputs |
| ------ | ------- |
| `workgroup_name` · `results_location` (s3) · `kms_key_arn` · `bytes_scanned_cutoff` (≥10 MB; we use **100 MB**) | `workgroup_name` |

`enforce_workgroup_configuration = true` so clients can't bypass the scan cap or the results location. Results are SSE-KMS-encrypted with the lake CMK.

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
| **1**  | Storage + batch ingest (Bronze)                          | `storage.tf`, `ingest.tf`                 | `kms`, `s3` (×3: bronze/silver/gold), `secret`, `iam_lambda`, `lambda`, `eventbridge`      | ✅ DEPLOYED to AWS (acct 724166961779); LocalStack create/update/destroy demoed |
| **2**  | Catalog + first SQL                                      | `catalog.tf`                              | `glue_catalog` (DB + `bronze_prices` table, partition projection), `athena` (workgroup, 100 MB cap), `athena_results` bucket — all gated by `enable_catalog` | ✅ DEPLOYED to AWS; LocalStack via the DuckDB twin (catalog gated off) |
| **3**  | Transform to Silver + Gold | `transform.tf` | (3d) inline Glue Spark job + IAM, gated `enable_glue_spark` (default off). **Silver/Gold Iceberg tables are built by dbt at runtime — not Terraform.** | ✅ SQL/Iceberg path DEPLOYED (Athena); Glue job account-blocked → gated off |
| **4**  | Streaming (real-time trades) | `streaming.tf` | inline Kinesis stream + consumer Lambda (reuses `lambda` module) + SQS DLQ + IAM + event-source mapping + `bronze_trades` Glue table — all gated `enable_streaming` (default off). **`silver_trades`/`gold_latest_price` built by dbt at runtime.** | ✅ LocalStack-validated end-to-end; Kinesis account-blocked on AWS → gated off |
| **5**  | RAG assistant (hybrid) | `rag.tf` | inline 3× least-privilege IAM + 3× `module.lambda_rag_*` (ingest/index/assistant, reuse `lambda` module) — all gated `enable_rag` (default off, needs `enable_catalog`). **The `rag/` app runs locally (Ollama+DuckDB+FAISS-in-S3); Lambdas are the deploy shape.** | ✅ Local-validated (CLI/API/UI); AWS-deferred (Bedrock access + faiss layer) → gated off |
| **6+** | Orchestration · CI/CD | _tbd_                                       | _Not yet built (e.g. Step Functions, GitHub Actions deploy)._ | ⏳ planned                                         |

### Resource names created in Stage 1 (`environment = dev`)

- KMS key + `alias/marketpulse-dev-kms-lake`
- `marketpulse-dev-bucket-bronze-<hex>` · `…-silver-<hex>` · `…-gold-<hex>`
- `marketpulse-dev-secret-marketdata-apikey`
- `marketpulse-dev-role-lambda-ingest` (+ inline `…-policy`)
- `marketpulse-dev-lambda-batch-ingest` (+ log group `/aws/lambda/marketpulse-dev-lambda-batch-ingest`)
- `marketpulse-dev-rule-ingest-schedule`

### Resource names created in Stage 2 (`environment = dev`)

- Glue database `marketpulse_dev` + table `bronze_prices` (partition projection on `snapshot_date`/`snapshot_hour`; no crawler)
- Athena workgroup `marketpulse-dev-athena-analytics` (100 MB per-query scan cap, SSE-KMS results)
- `marketpulse-dev-bucket-athena-results-<hex>` (dedicated, encrypted results bucket)

The Stage-2 catalog is gated by **`enable_catalog`** (default `true` for AWS; pass `-var="enable_catalog=false"` for LocalStack, where Glue/Athena are Pro — the local query twin is **DuckDB**). `moved` blocks migrate the count-gate state with no recreate. For local runs, force a local state file via a gitignored `infra/localstack_backend_override.tf` (`backend "local" {}`) — `tflocal` otherwise reads the real AWS state (see [stage-2 doc](stages/stage-2-catalog-first-sql.md)).

### Resource names created in Stage 3 (`environment = dev`)

Only the optional **Glue Spark learning job** is Terraform-managed (gated by `enable_glue_spark`, default **off**):

- `marketpulse-dev-role-glue-bronze-to-silver` (+ inline `…-policy`)
- `marketpulse-dev-glue-bronze-to-silver` (Glue 4.0, 2× G.1X) + script object `glue/scripts/bronze_to_silver.py` in the athena-results bucket

The **Silver/Gold Iceberg tables** (`silver_prices`, `gold_daily_ohlc`, `gold_price_moving_avg`, `gold_volatility`, `gold_market_movers`) are **not** Terraform — **dbt** (`src/transform/dbt/`) creates them at runtime in the `marketpulse_dev` Glue DB, a data-plane operation like the Lambda writing Bronze (see [§2](#2-scope--what-terraform-does-and-does-not-do)). The local twin is **DuckDB**; see the [stage-3 doc](stages/stage-3-transform-silver-gold.md).

**Glue job account-blocked:** `Glue: CreateJob` returns `AccessDeniedException: Account … is denied access` (account-level Glue-ETL restriction on the new AWS Free Plan — the catalog still works, only ETL jobs are blocked). The live run is deferred to an unrestricted account; the reviewed IaC stays gated off.

### Resource names created in Stage 4 (`environment = dev`)

All gated by **`enable_streaming`** (default **off**) — the stack is **$0** until a deliberate test window:

- `marketpulse-dev-stream-trades` (Kinesis, 1 shard provisioned, SSE-KMS, 24h retention)
- `marketpulse-dev-dlq-trades` (SQS dead-letter queue, CMK-encrypted, 14-day retention)
- `marketpulse-dev-role-lambda-trades-consumer` (+ inline `…-policy`)
- `marketpulse-dev-lambda-trades-consumer` (+ log group `/aws/lambda/…`) — reuses the `lambda` module, **no awswrangler layer** (boto3-only NDJSON consumer)
- the Kinesis → consumer **event-source mapping** (`LATEST`, batch 100 / 5s, 2 retries, bisect, on-failure → DLQ)
- Glue table `bronze_trades` (JSON SerDe over `bronze/trades/`, partition projection) — also needs `enable_catalog`

The **`silver_trades`/`gold_latest_price`** tables are **not** Terraform — **dbt** builds them at runtime (DuckDB locally, Athena Iceberg on AWS), a data-plane operation. See the [stage-4 doc](stages/stage-4-streaming.md).

**Kinesis account-blocked:** `Kinesis: CreateStream` returns `SubscriptionRequiredException: The AWS Access Key Id needs a subscription for the service` (account-level Free-Plan restriction, same class as the Glue-ETL block). The live apply created the other 7 streaming resources cleanly before the stream call was refused; the partial deploy was torn down (~$0). The pipeline is fully validated on LocalStack; the live AWS run is deferred to an unrestricted account, the reviewed IaC stays gated off.

### Resource names created in Stage 5 (`environment = dev`)

All gated by **`enable_rag`** (default **off**, requires `enable_catalog`) — **$0** until applied; the `rag/` app runs locally at $0:

- `marketpulse-dev-role-lambda-rag-ingest` / `…-rag-index` / `…-rag-assistant` (+ inline `…-policy` each)
- `marketpulse-dev-lambda-rag-ingest` (RSS → `bronze/docs/`) · `…-rag-index` (embed → `gold/rag/<env>/` FAISS) · `…-rag-assistant` (retrieve + templated Gold query → Bedrock) — all reuse the `lambda` module
- the FAISS index objects `gold/rag/<env>/{index.faiss,meta.json}` (data-plane, written by the index Lambda / local CLI — not Terraform)

**AWS-deferred (gated off):** the assistant is fully validated **locally** (CLI + FastAPI + Streamlit; Ollama + DuckDB-over-the-dbt-Gold-DB + FAISS-in-LocalStack-S3). A real AWS deploy needs (a) **Bedrock model access** granted in the console and (b) a Lambda **layer or container image** carrying `faiss`+`numpy` (binary deps can't ride a plain zip). The reviewed IaC stays gated off. See the [stage-5 doc](stages/stage-5-rag.md).

### Remote state backend (Stage 1)

State lives in **S3** (`marketpulse-dev-tfstate-<account>`) with a **DynamoDB lock**
(`marketpulse-dev-tflock`), created by [`infra/bootstrap/`](../infra/bootstrap/) (run once with its
own local state). `infra/` wires it via a partial `backend "s3" {}` — concrete values in the
gitignored `infra/backend.hcl` (`terraform init -backend-config=backend.hcl`). LocalStack runs still
use local state. _(Deferred cleanup: `dynamodb_table` → `use_lockfile`, dropping DynamoDB.)_

---

## Appendix — directory tree

```
infra/
├── main.tf                  provider + default_tags + required_providers
├── variables.tf             region · environment · owner · awswrangler_layer_arn · ingest_schedule
├── storage.tf               random_id + module.kms + module.s3 (×3)
├── ingest.tf                module.secret + iam_ingest + archive_file + lambda_ingest + eventbridge_ingest
├── catalog.tf               (Stage 2) athena_results bucket + glue_catalog + athena — gated by enable_catalog
├── transform.tf             (Stage 3d) Glue Spark job + IAM (inline) — gated by enable_glue_spark (default off)
├── streaming.tf             (Stage 4) Kinesis stream + consumer Lambda + SQS DLQ + IAM + ESM + bronze_trades — gated by enable_streaming (default off)
├── rag.tf                   (Stage 5) 3× RAG Lambdas (ingest/index/assistant) + least-privilege IAM — gated by enable_rag (default off); AWS-deferred
├── outputs.tf               bucket_names · kms/secret/role/function/rule · glue_database · athena_workgroup · athena_results_bucket · glue_bronze_to_silver_job · trades_stream_name · trades_dlq_url · rag_assistant_function_name
├── .terraform.lock.hcl      provider version locks (committed)
└── modules/
    ├── kms/                 CMK + alias
    ├── s3/                  private + SSE-KMS + versioned bucket + lifecycle expiry
    ├── secret/              Secrets Manager container (value out-of-band)
    ├── iam_lambda/          least-privilege role + inline policy
    ├── lambda/              function + log group
    ├── eventbridge/         schedule rule + target + invoke permission
    ├── glue_catalog/        (Stage 2) Glue database + bronze_prices table (partition projection)
    └── athena/              (Stage 2) Athena workgroup (100 MB scan cap) + SSE-KMS results
```

Each module folder is `main.tf` (resources) + `variables.tf` (inputs) + `outputs.tf` (returns).
