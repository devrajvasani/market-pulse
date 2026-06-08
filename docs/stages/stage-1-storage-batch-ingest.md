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
- **Ingestion code (`src/ingestion/batch/`)** — `coingecko.py` (stdlib `urllib` + retry/backoff), `ingest.py` (fetch → DataFrame → `wr.s3.to_parquet` `overwrite_partitions`, idempotent per **hourly** snapshot; type-pinned 24-column Bronze schema), `handler.py` (Lambda entry), `main()` (`make run-local`). Slim deps so the zip stays tiny; pandas/awswrangler come from the layer.
- **Remote state backend** — `infra/bootstrap/` creates an S3 state bucket + DynamoDB lock table; `infra/` uses a partial `backend "s3"` (config in gitignored `backend.hcl`).
- **Tests** — 29 moto/unit tests (fetch + multi-window request, transform + null-safety, key resolution, idempotent hourly write + type-pinning, CLI entry).

## Bronze schema & partitioning (widened 2026-06-06, pre-Stage-2)

**Why widen:** Bronze is the only *raw*, append-only record — a field dropped at ingest can never be backfilled for past dates. The first cut kept 11 fields; before Stage 2 (catalog + SQL) we expanded to the full analytically-useful surface of CoinGecko `/coins/markets` with production names, so Stage 3 (OHLC / rolling / volatility) and Stage 5 (RAG) have what they need.

**Why hourly:** the schedule fires hourly, so we **retain each hourly snapshot** at `prices/snapshot_date=YYYY-MM-DD/snapshot_hour=HH/` instead of overwriting one daily file — an hourly time series. `overwrite_partitions` keeps a same-hour re-run idempotent (replaces only that hour); earlier hours accumulate.

**24 data + 2 partition columns** (`snake_case`, currency-neutral amounts, ISO-string timestamps left raw for Silver to cast):

| group | columns |
|---|---|
| identity | `coin_id`, `coin_symbol`, `coin_name`, `quote_currency` |
| price / market | `current_price`, `market_cap`, `market_cap_rank` (bigint), `fully_diluted_valuation`, `total_volume` |
| 24h range | `high_24h`, `low_24h` |
| price moves | `price_change_24h`, `price_change_pct_24h`, `price_change_pct_1h`, `price_change_pct_7d` |
| cap moves | `market_cap_change_24h`, `market_cap_change_pct_24h` |
| supply | `circulating_supply`, `total_supply`, `max_supply` |
| all-time high | `ath`, `ath_change_pct` |
| timestamps | `source_updated_at` (vendor), `ingested_at` (ours) |
| partitions | `snapshot_date`, `snapshot_hour` |

**Type-pinned** (`awswrangler dtype=`): each column gets an explicit Athena/Glue type, so the Parquet schema is identical across partitions even when a nullable field (e.g. `max_supply`, null for ETH) is null for every coin in a pull — avoids `HIVE_BAD_DATA` against the manually-defined Glue table in Stage 2.

**Request:** `price_change_percentage=1h,24h,7d` (adds the 1h/7d move columns). **Dropped as noise:** `image`, `roi` (irregular nested object), the 24h `_in_currency` duplicate, rehypothecated rank, the ATL / ATH-date family, `sparkline`.

**Verified live** (`snapshot_hour=22`): 26 columns read back with correct types (`market_cap_rank` `Int64`, amounts `double`); `max_supply` null for ETH handled cleanly; `price_change_pct_7d` BTC −16.2% / ETH −20.6%.

## Coin universe & retention (widened 2026-06-08, pre-Stage-3)

**Top-100 by market cap.** Stage 1 shipped a fixed 2-coin list (BTC + ETH); to give Stage 3 (OHLC / rolling / volatility / cross-sectional analytics) and Stage 5 (RAG) a meaningful cross-section, the default universe is now the **top 100 coins by market cap**, fetched in one CoinGecko call (`order=market_cap_desc&per_page=100&page=1`). `coingecko.py` gains `fetch_top_markets(count)` (validated `1..250`, the per-page max) alongside the by-id `fetch_markets` — both share one validated `_markets()` helper. `ingest.run_ingestion` fetches top-N by default; override with the **`INGEST_TOP_N`** env var, or pass explicit `coins=[...]` to bypass top-N. Schema, partitioning and idempotency are unchanged — just ~100 rows per hourly snapshot instead of 2.

**Cost is unaffected.** A snapshot is ≈30 KB Parquet (≈15 KB fixed + ~175 B/coin); a year of hourly top-100 snapshots is well under 1 GB → cents of S3. Athena's **10 MB-per-query floor** dominates either way, so per-query cost stays ~$0. The project ceiling is COMPUTE-driven (Glue DPU-hrs, Kinesis shard-hrs, Bedrock tokens), not row count.

**Noncurrent-version lifecycle.** More coins + idempotent same-hour re-writes mean the versioned buckets would otherwise accrue superseded versions indefinitely. The `s3` module now adds an `aws_s3_bucket_lifecycle_configuration` to **every** lake bucket (bronze/silver/gold + athena-results): expire **noncurrent** versions after 7 days and abort incomplete multipart uploads after 7 days. **Current object versions are never touched** — only superseded history and orphaned upload parts. Tunable via `noncurrent_version_expiration_days` / `abort_incomplete_multipart_upload_days` (both default 7).

**Apply:** the Lambda code redeploy (`terraform apply` — new `source_code_hash`) switches the live hourly pipeline to top-100; the lifecycle rule is a new resource added in-place to the existing buckets (`+`, non-destructive). **Verified:** `ruff` ✓ · `ruff format` ✓ · 42 tests ✓ (widen + a follow-up hardening pass: top-N URL/boundaries, `1..250` validation, explicit-coins, empty-result, `INGEST_TOP_N` override, retry/backoff) · `terraform validate` ✓.

## The full IaC lifecycle (learned hands-on)
- **LocalStack (free):** `tflocal apply` (create `+23`) → in-place **update** (`~` schedule) → **destroy**.
- **Real AWS (acct `724166961779`, us-east-1):** bootstrapped the remote backend → cost + infra review → `terraform apply` (`+23`) → Lambda **verified end-to-end** (secret read → CoinGecko → SSE-KMS Parquet in bronze, `StatusCode 200`, `rows=2` — the original BTC+ETH cut, now 100 per snapshot; see *Coin universe & retention* above).

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

### The complete procedure (exactly as performed in Stage 1)

**A · Run on LocalStack** — free, repeatable, no creds:
```powershell
# 1. ENABLE LocalStack
docker compose -f infrastructure/localstack/docker-compose.yml up -d
bash scripts/wait_for_localstack.sh                       # gate until S3 is ready

# 2. PROVISION (create / update / destroy all use these same commands)
cd infra
uv run tflocal init
uv run tflocal plan                                       # +23 to add  (or ~ N to change on an edit)
uv run tflocal apply                                      # yes

# 3. RUN the ingestion against LocalStack
$env:BRONZE_BUCKET = (uv run tflocal output -json bucket_names | ConvertFrom-Json).bronze
$env:APP_ENV = "local"
cd ..
uv run python -m src.ingestion.batch.ingest               # -> Parquet in LocalStack bronze

# 4. VERIFY
uv run python -c "import os,boto3; from config import settings; c=boto3.client('s3',endpoint_url=settings.aws_endpoint_url(),region_name=settings.aws_region()); print('\n'.join(o['Key'] for o in c.list_objects_v2(Bucket=os.environ['BRONZE_BUCKET']).get('Contents',[])))"

# 5. TEAR DOWN (free) + DISABLE
cd infra; uv run tflocal destroy                          # yes  (force_destroy empties bronze)
docker compose -f infrastructure/localstack/docker-compose.yml down
```

**B · Deploy + run on live AWS** — one-time setup, then deploy:
```powershell
# 1. CREDS (once): create an access key for the marketpulse-admin IAM user, then put it in
#    ~/.aws/credentials ([marketpulse-admin]) + ~/.aws/config ([profile marketpulse-admin]).
$env:AWS_PROFILE = "marketpulse-admin"
uv run python -c "import boto3; print(boto3.client('sts',region_name='us-east-1').get_caller_identity())"   # expect the real 12-digit account

# 2. BOOTSTRAP the remote state backend (once) — creates the S3 state bucket + DynamoDB lock
cd infra/bootstrap
terraform init
terraform apply -var="owner=devraj-vasani"                # yes  -> state_bucket + lock_table
terraform output

# 3. WIRE infra/ to that backend (done in Stage 1: backend "s3" {} in main.tf + values in backend.hcl)
cd ..
terraform init -reconfigure "-backend-config=backend.hcl"  # state now lives in S3

# 4. DEPLOY
terraform plan                                            # review: +23, real ARNs, force_destroy, layer ARN
terraform apply                                           # yes

# 5. SET THE SECRET out-of-band (reads the CoinGecko key from .env -> Secrets Manager; nothing typed)
cd ..
uv run python -c "import boto3,json,os; from dotenv import load_dotenv; load_dotenv(); boto3.client('secretsmanager',region_name='us-east-1').put_secret_value(SecretId='marketpulse-dev-secret-marketdata-apikey', SecretString=json.dumps({'api_key':os.environ['MARKETDATA_API_KEY']}))"

# 6. VERIFY on real AWS — invoke the Lambda, then list bronze
uv run python -c "import boto3; r=boto3.client('lambda',region_name='us-east-1').invoke(FunctionName='marketpulse-dev-lambda-batch-ingest'); print(r['StatusCode']); print(r['Payload'].read().decode())"
uv run python -c "import boto3; c=boto3.client('s3',region_name='us-east-1'); print('\n'.join(o['Key'] for o in c.list_objects_v2(Bucket='marketpulse-dev-bucket-bronze-65fa4d26').get('Contents',[])))"

# 7. TEAR DOWN before credits expire (June 18) — one command thanks to force_destroy
cd infra; terraform destroy                               # yes
```

**C · The switch we performed (local ➜ cloud)** — because `tflocal` and `terraform` share the local `terraform.tfstate`, we separated their state:
```powershell
cd infra
uv run tflocal destroy                          # 1. tear down LocalStack + clear local state
# 2. add backend "s3" {} to main.tf, create backend.hcl from the bootstrap output
terraform init -reconfigure "-backend-config=backend.hcl"   # 3. AWS now reads/writes S3 state
terraform apply                                 # 4. deploy for real
```
> The first time, `init` may warn `Too many command line arguments` in PowerShell — quote it: `"-backend-config=backend.hcl"`. **Rule:** local = local state, AWS = S3 backend; never share one state file.

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
- Medallion lake, **Bronze = raw, type-pinned 24-col Parquet partitioned by `snapshot_date` + `snapshot_hour`** (hourly time series); idempotent `overwrite_partitions` (per-hour-safe re-runs).
- pandas/awswrangler from the **AWS-managed layer**, not the zip (slim package; `urllib` over `requests`).
- Secret value **out-of-band** (the state trap) — CoinGecko key in `.env` (local) **and** Secrets Manager (cloud).
- `force_destroy=true` on lake buckets (dev: one-command teardown; data is re-ingestible).
- **Remote S3 backend + DynamoDB lock** (chose "set up now" over deferring).

## Verified
`ruff` ✓ · 29 tests ✓ · `terraform validate` ✓ · LocalStack create/update/destroy ✓ · AWS apply `+23` ✓ · Lambda invoke `200` → 26-col Parquet in bronze (`snapshot_date`/`snapshot_hour`) ✓.

## Left to do
- **Console (user):** create the Resource Group + activate the `project` cost-allocation tag (Stage 0 carry-over — now tagged resources exist).
- **Before June 18:** `terraform destroy` (+ export any data) — credit-expiry hygiene; one command thanks to `force_destroy`.
- **Deferred cleanup:** backend `dynamodb_table` → `use_lockfile` (drop DynamoDB).
