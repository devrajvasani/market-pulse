# Stage 4 — Real-time streaming (trades)

> **High-level summary.** Add a **near-real-time** path alongside the hourly batch: stream live
> **trades** from Coinbase over a WebSocket → **Kinesis** → a **consumer Lambda** that lands raw
> **NDJSON** in Bronze → **dbt** refines them into `silver_trades` (typed, deduped) and the
> `gold_latest_price` serving mart ("what's the price *right now*"). Config-swappable: the exact same
> producer/consumer run on **LocalStack** and **AWS**; DuckDB or Athena reads the Bronze. Detail lives
> in the code + [terraform-architecture.md](../terraform-architecture.md); this is the what/why.

| | |
| --- | --- |
| **Goal** | Live trades → Kinesis → Lambda → Bronze NDJSON → Silver (`silver_trades`) → Gold (`gold_latest_price`), DQ-gated, with a dead-letter queue. |
| **Status** | ✅ **Pipeline DONE & validated end-to-end on LocalStack** (producer → Kinesis → consumer → Bronze → dbt Silver/Gold, live BTC/ETH, idempotent). The **AWS-live Kinesis run is account-blocked** (Free Plan) → future scope; the IaC is real-AWS-valid (5/8 resources created on the live apply before `CreateStream` was refused; torn down at ~$0). |
| **Primary path** | Kinesis Data Streams (1 shard, provisioned) + consumer Lambda (**boto3-only NDJSON**, no layer) + SQS DLQ; dbt for Silver/Gold. |
| **Cost** | **$0 baseline** — every streaming resource is gated by `enable_streaming` (default `false`). A test window is ~**$0.015/shard-hour** Kinesis (~$0.03 for 2h); Lambda/SQS/Athena negligible (free-tier / KB data). |

## What was built

**Streaming transport + ingest** (`infra/streaming.tf`, `src/ingestion/streaming/`):

| Component | What it is |
| --- | --- |
| `producer.py` | Subscribes to the Coinbase `matches` channel for a set of products, normalises each trade, and `put_records` batches to Kinesis. Runs for a **bounded window** (`STREAM_WINDOW_SECONDS`) then exits — never idle. `DRY_RUN=1` logs without writing. |
| `aws_kinesis_stream.trades` | 1-shard **provisioned** stream, SSE-KMS (lake CMK), 24h retention. |
| `consumer.py` (Lambda) | Triggered by the Kinesis event-source mapping; decodes the batch and writes **one NDJSON file** to `bronze/trades/snapshot_date=…/snapshot_hour=…/{last_sequence_number}.json` using **boto3 only**. |
| `aws_sqs_queue.trades_dlq` | Dead-letter queue (CMK-encrypted, 14-day retention) — the ESM parks batches that keep failing after retries. |
| event-source mapping | Kinesis → consumer, `LATEST`, batch 100 / 5s window, 2 retries, bisect-on-error, on-failure → DLQ. |
| `aws_glue_catalog_table.bronze_trades` | Athena read-side: JSON-SerDe table over `bronze/trades/` with partition projection (`snapshot_date`/`snapshot_hour`). Gated on `enable_streaming && enable_catalog` (AWS only; DuckDB reads the NDJSON directly). |

**dbt models** — same project/engines as Stage 3:

| Model | Layer | What it is |
| --- | --- | --- |
| `silver_trades` | Silver | typed (NDJSON strings → `double`/`bigint`/timestamps via the `parse_iso_timestamp` macro), **deduped to one row per `(product_id, trade_id)`** (at-least-once delivery can re-land a trade). |
| `gold_latest_price` | Gold | the **latest trade per `product_id`** (row_number over `event_time desc, ingested_at desc`) — the streaming serving mart. |

**Data-quality gate** — `silver_trades` asserts `not_null` keys/timestamps, `price > 0`, and a **`unique_combination_of_columns(product_id, trade_id)`** natural-key test; `gold_latest_price` asserts one `unique`/`not_null` row per `product_id` with a positive `latest_price`. Offline `pytest` (`tests/test_*`) exercises the producer normalisation, the consumer partition/idempotency logic, and the Silver dedup on DuckDB fixtures — no infra, $0.

## The config-swap (how local ↔ AWS works)

The **same producer and consumer code** run in both places, selected only by `AWS_ENDPOINT_URL`:

| | LocalStack (free twin) | AWS |
| --- | --- | --- |
| Kinesis / Lambda / SQS | LocalStack (`AWS_ENDPOINT_URL=…:4566`, `test`/`test` creds) | real services (`AWS_PROFILE`) |
| Consumer writes | `bronze/trades/…` NDJSON via boto3 | identical |
| **Bronze read (dbt)** | DuckDB `read_json_auto(DBT_TRADES_GLOB, format='newline_delimited', hive_partitioning=true)` | Athena Glue table `bronze_trades` (JSON SerDe) |

The **deliberate portability choice**: the consumer writes **NDJSON with boto3 only** — no pandas/pyarrow/awswrangler — so it needs **no Lambda layer** and runs unchanged on LocalStack (where the awswrangler layer is a Pro feature). dbt's `_sources.yml` carries both read paths; the model SQL never changes.

## Runbook — running it (offline · LocalStack · AWS)

**Offline tests — no infra, $0:**
```powershell
make test     # pytest: producer normalise + consumer partition/idempotency + silver_trades dedup (DuckDB)
```

**Local — full pipeline on LocalStack (the free twin).** Prereqs: Docker running + LocalStack up, and the gitignored `infra/localstack_backend_override.tf` (`backend "local" {}`) present so `tflocal` uses **local** state, not the real S3 backend (see the [stage-2 doc](stage-2-catalog-first-sql.md)). All commands are **PowerShell**.
```powershell
# 1. LocalStack up
docker compose -f infrastructure/localstack/docker-compose.yml up -d ; bash scripts/wait_for_localstack.sh

# 2. Deploy the streaming stack locally (catalog off -> DuckDB read side; no awswrangler layer)
Push-Location infra
uv run tflocal apply -var enable_streaming=true -var enable_catalog=false -var awswrangler_layer_arn=""
$env:BRONZE_BUCKET = (uv run tflocal output -json bucket_names | ConvertFrom-Json).bronze
Pop-Location

# 3. Stream a bounded window of live trades into Kinesis (host -> LocalStack). The deployed
#    consumer Lambda fires on the event-source mapping and lands Bronze NDJSON. The stream
#    name defaults to marketpulse-dev-stream-trades, so STREAM_NAME isn't needed locally.
$env:AWS_ENDPOINT_URL="http://localhost:4566"; $env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"; $env:AWS_DEFAULT_REGION="us-east-1"
$env:STREAM_WINDOW_SECONDS="120"        # tip: set $env:DRY_RUN="1" first to validate the live feed without writing
uv run python -m src.ingestion.streaming.producer

# 4. Build Silver + Gold over the LocalStack Bronze trades (DuckDB; S3 endpoint defaults to localhost:4566)
$env:DBT_TRADES_GLOB = "s3://$($env:BRONZE_BUCKET)/trades/**/*.json"
Push-Location src/transform/dbt
dbt build --select silver_trades gold_latest_price --profiles-dir . --project-dir .
Pop-Location

# 5. Tear down (free)
Push-Location infra
uv run tflocal apply -var enable_streaming=false -var enable_catalog=false -var awswrangler_layer_arn=""
Pop-Location
```
Inspect: `gold_latest_price` in `src/transform/dbt/target/marketpulse.duckdb` — one row per product with the most recent price.
> **Containerised alternative** for step 3 (the bounded-window helper `scripts/run-stream-window.sh` builds + runs the producer in Docker) — it's a **bash** script, so run it from **Git Bash / WSL**, not PowerShell: `MINUTES=2 bash scripts/run-stream-window.sh` (from inside the container LocalStack is reached at `host.docker.internal:4566`, which the script defaults to).

**AWS live — account-blocked here (the commands for an unrestricted account).** All **PowerShell**, with real creds:
```powershell
# Use the real profile; make sure no LocalStack endpoint / test creds linger from a local run
$env:AWS_PROFILE="marketpulse-admin"
Remove-Item Env:AWS_ENDPOINT_URL, Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue

# 1. Deploy the streaming stack
terraform -chdir=infra apply -var enable_streaming=true        # stream + consumer + DLQ + bronze_trades

# 2. Stream a bounded window into REAL Kinesis (no endpoint override = real AWS; name defaults as above)
$env:STREAM_WINDOW_SECONDS="120"
uv run python -m src.ingestion.streaming.producer

# 3. Build Silver/Gold on Athena (Iceberg), like Stage 3
$names = terraform -chdir=infra output -json bucket_names | ConvertFrom-Json
$res   = terraform -chdir=infra output -raw athena_results_bucket
$env:DBT_SILVER_DATA="s3://$($names.silver)/"; $env:DBT_GOLD_DATA="s3://$($names.gold)/"
$env:DBT_ATHENA_STAGING="s3://$res/query-results/"; $env:AWS_DEFAULT_REGION="us-east-1"
dbt build --target athena --select silver_trades gold_latest_price --profiles-dir src/transform/dbt --project-dir src/transform/dbt
# Verify (Athena workgroup marketpulse-dev-athena-analytics):  SELECT * FROM gold_latest_price ORDER BY product_id;

# 4. TEAR DOWN immediately -- Kinesis bills per shard-hour; the producer window does NOT remove the stream
terraform -chdir=infra apply -var enable_streaming=false
```
> On **this** account step 1 fails at `Kinesis: CreateStream` → `SubscriptionRequiredException` (see below). On an unrestricted account it runs end-to-end; **flip `enable_streaming=false` the moment the window ends** — the stream stays live until you do.
> Containerised producer alternative for step 2 (Git Bash / WSL): `AWS_ENDPOINT_URL= AWS_PROFILE=marketpulse-admin MINUTES=2 bash scripts/run-stream-window.sh`.

**Switching local ↔ AWS — config only, no code change.** The same producer, consumer, and dbt models run in both places; you change only the environment:

| Knob | Local (LocalStack) | AWS |
| --- | --- | --- |
| `AWS_ENDPOINT_URL` | `http://localhost:4566` | **unset** (real endpoints) |
| creds | `test` / `test` | `AWS_PROFILE=marketpulse-admin` |
| Terraform CLI | `tflocal` (local state) | `terraform` (real S3 backend) |
| dbt `--target` | `duckdb` (reads NDJSON via `DBT_TRADES_GLOB`) | `athena` (reads the `bronze_trades` Glue table, writes Iceberg) |

The consumer is unchanged because it derives its S3 endpoint from `AWS_ENDPOINT_URL` (`settings.aws_endpoint_url()`), which LocalStack injects into the Lambda automatically and AWS leaves unset.

## Key decisions

- **NDJSON + boto3-only consumer.** No Parquet libraries in the hot path → no Lambda layer → the *same* consumer runs on LocalStack and AWS. Silver does the typing; the cost is a JSON-SerDe Athena table instead of Parquet (fine at streaming volumes).
- **Idempotent by construction.** The S3 key is the batch's **last Kinesis sequence number**, so a retried batch overwrites the same file; Silver then dedups by the natural key. Re-runs never duplicate.
- **Natural key = `(product_id, trade_id)`.** Coinbase `trade_id` is a **per-product** sequence, not globally unique — deduping on `trade_id` alone would silently drop a real trade *and* a global `unique` test would mask it. (Caught by the pre-PR review; see below.)
- **Provisioned 1-shard, gated off.** At 1 shard, provisioned (`$0.015/shard-hr`) beats on-demand (`$0.08/shard-hr`); `enable_streaming` (default `false`) keeps the whole stack at **$0** until a deliberate test window.
- **`LATEST` starting position.** The stream is created in the same apply, so there are no pre-existing records to miss.

## The Kinesis account-block → future scope

The live `terraform apply -var enable_streaming=true` created **5 of 8** resources cleanly (consumer Lambda + log group, SQS DLQ, IAM role, `bronze_trades` table), then **`Kinesis: CreateStream` returned `SubscriptionRequiredException: The AWS Access Key Id needs a subscription for the service`** — an **account-level** restriction on the new AWS **Free Plan** (the same class as the Stage 3 Glue-ETL block; Kinesis itself is not subscribable on this tier). Resolution, mirroring Stage 3:
- The streaming IaC + code are **kept** (`streaming.tf`, gated `enable_streaming=false`); the partial deploy was **torn down** (`~$0` — the stream never existed, the rest was free-tier/idle for minutes).
- The **one live AWS data-flow run is future scope** — run on an unrestricted/paid account. Everything is **fully validated on LocalStack**, which has no such tier limit.

**Free-Plan-restricted services** (account tier, both hit during the build): **Kinesis Data Streams** and **Glue ETL jobs**. Everything core works — S3, Lambda, SQS, KMS, Athena, the **Glue Data Catalog** (only ETL *jobs* are blocked, not catalog tables), EventBridge, Secrets Manager.

## Pre-PR review outcome (multi-agent: cost · infra · adversarial verify · completeness critic)

Final verdict **GO** (after one fix). The review's value showed in two places:
- **A false-positive was killed.** The infra reviewer flagged a "medium": the CMK-encrypted DLQ supposedly needs an SQS resource policy or the ESM on-failure write fails. Adversarial verification refuted it — a Kinesis **poll-based** ESM writes the DLQ via the **function execution role** (not the Lambda service principal, which is the async-invoke case), and that role **already holds** `sqs:SendMessage` + `kms:GenerateDataKey/Decrypt` with the account's default KMS key policy delegating to IAM. No code change — and an unnecessary resource avoided.
- **A real bug neither reviewer caught was fixed** (completeness critic): the `silver_trades` `(product_id, trade_id)` dedup key described above. One-line model change + a `unique_combination_of_columns` test + a cross-product regression test (which fails under the old key). `dbt parse` + 55 unit tests green.

## Definition of done

| DoD item | Status |
| --- | --- |
| Producer streams Coinbase trades → Kinesis | ✅ LocalStack (DRY_RUN validated the live feed; 167 trades) |
| Consumer writes Bronze NDJSON, idempotent, with a DLQ | ✅ LocalStack |
| `silver_trades` typed + deduped on the natural key, DQ-gated | ✅ DuckDB; `dbt parse` clean on the Athena config |
| `gold_latest_price` = latest trade per product | ✅ LocalStack |
| Live AWS run (real Kinesis) | ⏳ account-blocked (`SubscriptionRequiredException`) → future scope |

## Deferred / future scope

- **Live AWS Kinesis run** — execute the end-to-end flow once on an account where Kinesis is permitted.
- **Producer partial-failure retry** — `put_records` partial failures are logged but not retried; add backoff + raise on exhaustion (currently a rare silent-drop path under 1-shard load).
- **Per-partition batch grouping** — the consumer keys a whole batch off the first record's `event_time`; a batch straddling an hour/midnight boundary misplaces later records (bounded by the 5s window). Group by `(snapshot_date, snapshot_hour)` for a production version.
- **Strengthen the offline Silver test** — it currently runs a hand-copied SQL string, not the real model; wire it to the compiled model when dbt unit-testing allows.
- **KMS explicit key policy** + a runbook/Budget-Action teardown guard for the streaming window.

## Boundary note (Terraform vs dbt)

Like Stage 3, the **Silver/Gold tables are built by dbt at runtime** (data plane). Terraform builds the **streaming transport** — Kinesis, the consumer Lambda, the DLQ, the event-source mapping — and the `bronze_trades` **catalog** table (control plane). This is the same [§2 control-plane/data-plane boundary](../terraform-architecture.md#2-scope--what-terraform-does-and-does-not-do): Terraform builds the pipes; the consumer and dbt move the data.
