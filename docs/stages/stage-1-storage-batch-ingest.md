# Stage 1 — Storage Foundation & Batch Ingestion

**Status:** Done (2026-06-05) · Spec: `docs/plan/02_PROJECT_PLAN_IN_DEPTH.md` (Stage 1)

**Goal:** stand up the medallion S3 lake (Bronze/Silver/Gold) + a scheduled batch-ingestion Lambda that lands CoinGecko prices as Parquet in Bronze — built once, runnable **cloud ↔ local by config**.

## What was built (in repo)
- **Terraform (`infra/`)** — 6 reusable modules (`kms`, `s3`, `secret`, `iam_lambda`, `lambda`, `eventbridge`) composed in `storage.tf` + `ingest.tf`. Full write-up: [terraform-architecture.md](../terraform-architecture.md).
  - **KMS CMK** (auto-rotation) → SSE-KMS on all 3 buckets + the secret.
  - **3 medallion buckets** (bronze/silver/gold): all public access blocked, versioned, `force_destroy=true` (dev teardown).
  - **Secrets Manager container** for the CoinGecko key — value set **out-of-band**, never in state.
  - **Least-privilege IAM role** (no wildcards): write bronze, use the CMK, read the one secret, write own logs.
  - **Ingest Lambda** (python3.12, 256 MB, 120 s, AWS-managed pandas layer) + log group (14-day retention).
  - **EventBridge** `rate(1 hour)` schedule → invokes the Lambda.
- **Ingestion code (`src/ingestion/batch/`)** — `coingecko.py` (stdlib `urllib` + retry/backoff), `ingest.py` (fetch → DataFrame → `wr.s3.to_parquet` `overwrite_partitions`, idempotent by `dt`), `handler.py` (Lambda entry), `main()` (`make run-local`). Slim deps so the zip stays tiny; pandas/awswrangler come from the layer.
- **Remote state backend** — `infra/bootstrap/` creates an S3 state bucket + DynamoDB lock table; `infra/` uses a partial `backend "s3"` (config in gitignored `backend.hcl`).
- **Tests** — 26 moto/unit tests (fetch, transform, key resolution, idempotent write, CLI entry).

## The full IaC lifecycle (learned hands-on)
- **LocalStack (free):** `tflocal apply` (create `+23`) → in-place **update** (`~` schedule) → **destroy**.
- **Real AWS (acct `724166961779`, us-east-1):** bootstrapped the remote backend → cost + infra review → `terraform apply` (`+23`) → Lambda **verified end-to-end** (secret read → CoinGecko → SSE-KMS Parquet in bronze, `StatusCode 200`, `rows=2`).

## LocalStack ↔ Live AWS — the config swap (core concept)

The defining feature of this project: **the same code and the same Terraform run against either a free local emulator (LocalStack) or real AWS — chosen by *config*, never by changing code.** Develop and test for $0, deploy for real when ready.

### The mental model: two independent "rails"

Switching happens at **two separate layers**, each with its own switch:

| Layer | What it does | LocalStack switch | Live-AWS switch |
|---|---|---|---|
| **Infra** (Terraform) | *creates* buckets, KMS, Lambda… | **`tflocal`** — redirects AWS endpoints → `localhost:4566` | **`terraform`** — real endpoints + `~/.aws` creds |
| **Runtime** (app / boto3) | the code *using* those resources | **`APP_ENV=local`** → `AWS_ENDPOINT_URL=:4566` + dummy creds | **`APP_ENV=aws`** (or the deployed Lambda) → no override, real creds |

```
        IDENTICAL ARTIFACTS                          TWO TARGETS  (pick by config)
   ┌──────────────────────────┐
   │  infra/*.tf  (Terraform) │       ┌─────────── LOCAL · LocalStack · $0 ────────────┐
   │  src/ingestion (Python)  │ ────► │ tflocal       → endpoints = localhost:4566       │
   └──────────────────────────┘       │ APP_ENV=local → boto3/awswrangler → LocalStack   │
              │                        │ creds test/test · account 000000000000          │
              │                        │ state: local terraform.tfstate                  │
              │                        └─────────────────────────────────────────────────┘
              │                        ┌─────────── LIVE · real AWS ─────────────────────┐
              └──────────────────────► │ terraform     → real AWS endpoints              │
                                       │ APP_ENV=aws / Lambda IAM role → real AWS         │
                                       │ creds ~/.aws · account 724166961779             │
                                       │ state: S3 backend + DynamoDB lock               │
                                       └─────────────────────────────────────────────────┘
```

### How the redirect actually works (the magic)

- **LocalStack** is one Docker container (`marketpulse-localstack`) listening on `localhost:4566`, emulating every AWS service we use.
- **`tflocal`** is a thin wrapper around `terraform`: it generates a provider override pointing every AWS endpoint at `:4566` (with dummy creds), then runs terraform normally — so the *same* `.tf` builds in LocalStack instead of AWS.
- **App code** (`config/settings.py`) reads `APP_ENV`. For `local` it loads `config/environments/local.env`, which sets `AWS_ENDPOINT_URL=http://localhost:4566` + `test/test`. boto3 / awswrangler **honor `AWS_ENDPOINT_URL`**, so every S3 / Secrets call lands in LocalStack. For `aws`, there's no override → real AWS.
- **The tell** (how you know which you hit): the log line `Found endpoint for s3 via: environment_global` + account `000000000000` = LocalStack; real ARNs on account `724166961779` = AWS.

### Enabling / disabling LocalStack

```
ENABLE    docker compose -f infrastructure/localstack/docker-compose.yml up -d
          bash scripts/wait_for_localstack.sh          # gate until S3 is ready
DISABLE   docker compose -f infrastructure/localstack/docker-compose.yml down   (or stop in Docker Desktop)
```

**They are independent.** Turning LocalStack off does **not** affect live AWS — real `terraform` / boto3 use real endpoints + `~/.aws` creds and ignore LocalStack entirely. LocalStack only matters when you *explicitly* point at it (`tflocal`, or `AWS_ENDPOINT_URL`). The only thing to avoid is running a *local* command while expecting *cloud* behaviour (or vice-versa) — always know which rail you're on.

### Switching between them (and the state-separation gotcha)

**Infra** — just pick the command (`tflocal …` vs `terraform …`). **But** both default to the *same* local `terraform.tfstate`, so they collide. We separated them in Stage 1:

- **LocalStack** → local state (`terraform.tfstate` on disk).
- **Live AWS** → **remote S3 backend** (`backend "s3"`) — a different store entirely.

The switch we actually performed:
```
tflocal destroy                                  # 1. tear down LocalStack + clear local state
(add backend "s3" {} to main.tf)                 # 2. point infra/ at the S3 backend
terraform init -backend-config=backend.hcl       # 3. AWS now reads/writes S3 state
terraform apply                                  # 4. deploy for real
```
**Rule of thumb:** *local = local state, AWS = S3 backend — never share one state file between the two.*

**Runtime** — flip `APP_ENV`:
- `APP_ENV=local` → `local.env` → LocalStack endpoint, dummy creds, DuckDB/Ollama adapters.
- `APP_ENV=aws` → `aws.env` → real AWS, no override, Athena/Bedrock adapters.
- The **deployed Lambda** sets neither — running *inside* AWS it natively uses real endpoints, its IAM-role creds, and reads the key from Secrets Manager.

### When to use which

| Use **LocalStack** when… | Use **live AWS** when… |
|---|---|
| iterating on Terraform / code, testing, learning | you need the real thing: managed services, the live schedule, real data |
| you want $0 and instant feedback | cost + security are reviewed and you're ready to deploy |
| `tflocal apply` · `make run-local` | `terraform apply` (human-gated) + the hourly Lambda |

> Full command runbook + the plan-symbol guide (`+` / `~` / `-/+`): [terraform-architecture.md §8](../terraform-architecture.md).

## Pre-deploy reviews
- **cost-reviewer:** GO — **~$1.40/mo** (KMS $1 + Secrets $0.40; everything else free-tier). ~$0.61 to the June-18 credit expiry.
- **infra-reviewer:** fixed before deploy — `kms:Decrypt`, `s3:GetObject` + `s3:GetBucketLocation` (awswrangler), explicit EventBridge `target_id`, Lambda timeout 60→120 s. Deferred with reason: explicit KMS key policy (default defers to IAM), 7-day delete windows (teardown timeline). Remote backend: adopted.

## AWS now live
- Hourly pipeline running on account `724166961779`. Floor **~$1.40/mo** (new Free Plan auto-stops at $0 — no surprise bills).
- State: `s3://marketpulse-dev-tfstate-724166961779` + lock table `marketpulse-dev-tflock`.

## Key choices
- Medallion lake, **Bronze = raw Parquet partitioned by `dt`**; idempotent `overwrite_partitions` (safe re-runs).
- pandas/awswrangler from the **AWS-managed layer**, not the zip (slim package; `urllib` over `requests`).
- Secret value **out-of-band** (the state trap) — CoinGecko key in `.env` (local) **and** Secrets Manager (cloud).
- `force_destroy=true` on lake buckets (dev: one-command teardown; data is re-ingestible).
- **Remote S3 backend + DynamoDB lock** (chose "set up now" over deferring).

## Verified
`ruff` ✓ · 26 tests ✓ · `terraform validate` ✓ · LocalStack create/update/destroy ✓ · AWS apply `+23` ✓ · Lambda invoke `200` → Parquet in bronze ✓.

## Left to do
- **Console (user):** create the Resource Group + activate the `project` cost-allocation tag (Stage 0 carry-over — now tagged resources exist).
- **Before June 18:** `terraform destroy` (+ export any data) — credit-expiry hygiene; one command thanks to `force_destroy`.
- **Deferred cleanup:** backend `dynamodb_table` → `use_lockfile` (drop DynamoDB).
