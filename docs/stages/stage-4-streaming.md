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
| **Status** | ✅ **Pipeline DONE & validated end-to-end on LocalStack** via the **native event-source-mapping** path (producer → Kinesis → consumer Lambda **auto-fires** → Bronze → dbt Silver/Gold; live BTC/ETH; idempotent) — both as bounded windows and in a **continuous** mode (live in the LocalStack UI). The **AWS-live Kinesis run is account-blocked** (Free Plan) → future scope; the IaC is real-AWS-valid (torn down at ~$0). |
| **Primary path** | Kinesis Data Streams (1 shard, provisioned) + consumer Lambda (**boto3-only NDJSON**, no layer) + SQS DLQ; dbt for Silver/Gold. |
| **Cost** | **$0 baseline** — every streaming resource is gated by `enable_streaming` (default `false`). A test window is ~**$0.015/shard-hour** Kinesis (~$0.03 for 2h); Lambda/SQS/Athena negligible (free-tier / KB data). |

## What was built

**Streaming transport + ingest** (`infra/streaming.tf`, `src/ingestion/streaming/`):

| Component | What it is |
| --- | --- |
| `producer.py` | Subscribes to the Coinbase `matches` channel for a set of products, normalises each trade, and `put_records` batches to Kinesis. Runs a **bounded window** (`STREAM_WINDOW_SECONDS`, default 60 — never idle, the AWS default) **or continuously** (`STREAM_WINDOW_SECONDS=0`, Ctrl+C to stop, flushes on interrupt) for the local demo. `DRY_RUN=1` logs without writing. Config-only — no emulator/cloud branches. |
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

**Continuous-streaming tooling** (local learning extension; cloud-agnostic) — `producer.py`'s continuous mode (`STREAM_WINDOW_SECONDS=0`), `scripts/refresh_marts_loop.py` (loops `dbt build` + `dbt show gold_latest_price` every `REFRESH_SECONDS`, on any dbt target), and the LocalStack dashboard refresh dialed to **3s** so streamed Bronze appears ~live. On AWS the equivalents are the ESM (auto-drives the consumer) + the Stage-6 EventBridge schedule (drives the refresh) — no extra code.

## The config-swap (how local ↔ AWS works)

The **same producer and consumer code** run in both places, selected only by `AWS_ENDPOINT_URL`:

| | LocalStack (free twin) | AWS |
| --- | --- | --- |
| Kinesis / Lambda / SQS | LocalStack (`AWS_ENDPOINT_URL=…:4566`, `test`/`test` creds) | real services (`AWS_PROFILE`) |
| Consumer writes | `bronze/trades/…` NDJSON via boto3 | identical |
| **Bronze read (dbt)** | DuckDB `read_json_auto(DBT_TRADES_GLOB, format='newline_delimited', hive_partitioning=true)` | Athena Glue table `bronze_trades` (JSON SerDe) |

The **deliberate portability choice**: the consumer writes **NDJSON with boto3 only** — no pandas/pyarrow/awswrangler — so it needs **no Lambda layer** and runs unchanged on LocalStack (where the awswrangler layer is a Pro feature). dbt's `_sources.yml` carries both read paths; the model SQL never changes.

## Runbook — running it (offline · LocalStack · AWS)

> **⚠️ Packaging prereq — build the Lambda zip after any `src/` change.** The consumer
> Lambda's code is `infra/build/lambda`, staged by `scripts/build_lambda.sh` (it `cp -r src config`
> and strips `.gitkeep`). If you deploy without re-staging after adding/changing streaming code,
> the zip is **stale** and the consumer fails on every invoke with
> `Runtime.ImportModuleError: No module named 'src.ingestion.streaming'` → all batches dead-letter.
> Run it (bash / Git Bash) before `terraform`/`tflocal` apply:
> ```bash
> bash scripts/build_lambda.sh    # then terraform/tflocal zips infra/build/lambda and deploys
> ```

**Offline tests — no infra, $0:**
```powershell
make test     # pytest: producer normalise + consumer partition/idempotency + silver_trades dedup (DuckDB)
```

### Local — LocalStack (the free twin)
**Prereqs:** Docker up; the gitignored `infra/localstack_backend_override.tf` (`backend "local" {}`)
present so `tflocal` uses **local** state, not the real S3 backend (see the
[stage-2 doc](stage-2-catalog-first-sql.md)); and the Lambda package built (above). All commands
**PowerShell** unless noted.

```powershell
# 1. LocalStack up + wait for S3 (PowerShell-native health check)
docker compose -f infrastructure/localstack/docker-compose.yml up -d
do { Start-Sleep 2; $s = (Invoke-RestMethod http://localhost:4566/_localstack/health -ErrorAction SilentlyContinue).services.s3 } until ($s -in @('available','running'))

# 2. Deploy the streaming stack (catalog off -> DuckDB read side; no awswrangler layer).
#    NOTE: the 3 S3 lifecycle-config resources TIME OUT on LocalStack (~3 min, then 3 red errors)
#    -- that is a benign LocalStack limitation; the stream/consumer/DLQ/ESM/buckets all come up.
Push-Location infra
uv run tflocal apply -var enable_streaming=true -var enable_catalog=false -var awswrangler_layer_arn=""
$env:BRONZE_BUCKET = (uv run tflocal output -json bucket_names | ConvertFrom-Json).bronze
Pop-Location
```

**(a) A bounded window** — the cost-safe shape that mirrors AWS. The deployed consumer Lambda
fires on the **event-source mapping** automatically (no manual drain); it writes
`bronze/trades/…` NDJSON. Then dbt builds Silver + Gold:
```powershell
$env:AWS_ENDPOINT_URL="http://localhost:4566"; $env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"; $env:AWS_DEFAULT_REGION="us-east-1"
$env:DRY_RUN="0"; $env:STREAM_WINDOW_SECONDS="120"   # DRY_RUN=1 first to preview the feed without writing
uv run python -m src.ingestion.streaming.producer
$env:DBT_TRADES_GLOB = "s3://$($env:BRONZE_BUCKET)/trades/**/*.json"
Push-Location src/transform/dbt; dbt build --select silver_trades gold_latest_price --profiles-dir . --project-dir .; Pop-Location
```
Inspect: `gold_latest_price` in `src/transform/dbt/target/marketpulse.duckdb` — one row per product.

**(b) Continuous streaming (the live demo)** — two terminals + the dashboard:
```powershell
# Terminal A -- continuous producer (STREAM_WINDOW_SECONDS=0 = stream until Ctrl+C):
$env:AWS_ENDPOINT_URL="http://localhost:4566"; $env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"; $env:AWS_DEFAULT_REGION="us-east-1"
$env:DRY_RUN="0"; $env:STREAM_WINDOW_SECONDS="0"
uv run python -m src.ingestion.streaming.producer

# Terminal B (new window) -- periodic Silver/Gold refresh + latest-price print (~every 10s):
$env:AWS_ENDPOINT_URL="http://localhost:4566"; $env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"; $env:AWS_DEFAULT_REGION="us-east-1"
$env:BRONZE_BUCKET="<bronze-bucket-from-step-2>"
$env:DBT_TRADES_GLOB="s3://$($env:BRONZE_BUCKET)/trades/**/*.json"; $env:REFRESH_SECONDS="10"
uv run python scripts/refresh_marts_loop.py
```
Open the **LocalStack dashboard at http://localhost:8080** → **S3** → the bronze bucket → watch the
`trades/` object count climb (the UI auto-refreshes every **3s**). `Ctrl+C` both terminals to stop;
the infra stays up and idle (the producer + refresh loop are the only moving parts — closing them
ends the *active* streaming/transform, it does not tear anything down).

```powershell
# Tear down (free):
Push-Location infra; uv run tflocal apply -var enable_streaming=false -var enable_catalog=false -var awswrangler_layer_arn=""; Pop-Location
```

> **⚠️ LocalStack gotchas (learned the hard way):**
> - **Don't `docker compose … up --build ui`** without `--no-deps` — it recreates the **whole**
>   project (incl. LocalStack), and Community LocalStack does **not persist** Kinesis/Lambda/S3
>   across a restart → you'd lose everything and have to re-apply. Rebuild *only* the UI with
>   `docker compose -f infrastructure/localstack/docker-compose.yml up -d --no-deps --build ui`.
> - The **bounded-window helper** `scripts/run-stream-window.sh` (Docker container producer) is a
>   **bash** script — run it from **Git Bash / WSL**, not PowerShell: `MINUTES=2 bash scripts/run-stream-window.sh`.

### AWS live — account-blocked here (the commands for an unrestricted account)
All **PowerShell**, real creds. Same code; only the environment changes. On AWS the **ESM
auto-drives the consumer** (no terminal needed for ingestion) and the periodic refresh is the
**Stage-6 EventBridge schedule** rather than the local loop.
```powershell
$env:AWS_PROFILE="marketpulse-admin"
Remove-Item Env:AWS_ENDPOINT_URL, Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
bash scripts/build_lambda.sh                                    # stage the package (Git Bash)

terraform -chdir=infra apply -var enable_streaming=true         # stream + consumer + DLQ + bronze_trades
$env:STREAM_WINDOW_SECONDS="120"; $env:DRY_RUN="0"              # bounded window (cost); use 0 only if you truly want continuous
uv run python -m src.ingestion.streaming.producer

$names = terraform -chdir=infra output -json bucket_names | ConvertFrom-Json
$res   = terraform -chdir=infra output -raw athena_results_bucket
$env:DBT_SILVER_DATA="s3://$($names.silver)/"; $env:DBT_GOLD_DATA="s3://$($names.gold)/"
$env:DBT_ATHENA_STAGING="s3://$res/query-results/"; $env:AWS_DEFAULT_REGION="us-east-1"
dbt build --target athena --select silver_trades gold_latest_price --profiles-dir src/transform/dbt --project-dir src/transform/dbt
# Verify (Athena workgroup marketpulse-dev-athena-analytics):  SELECT * FROM gold_latest_price ORDER BY product_id;

terraform -chdir=infra apply -var enable_streaming=false        # TEAR DOWN -- Kinesis bills per shard-hour
```
> On **this** account step 1 fails at `Kinesis: CreateStream` → `SubscriptionRequiredException` (see below). On an unrestricted account it runs end-to-end; **flip `enable_streaming=false` the moment the window ends** — the stream stays live until you do.

**Switching local ↔ AWS — config only, no code change.** The same producer, consumer, and dbt models run in both places; you change only the environment:

| Knob | Local (LocalStack) | AWS |
| --- | --- | --- |
| `AWS_ENDPOINT_URL` | `http://localhost:4566` | **unset** (real endpoints) |
| creds | `test` / `test` | `AWS_PROFILE=marketpulse-admin` |
| Terraform CLI | `tflocal` (local state) | `terraform` (real S3 backend) |
| dbt `--target` | `duckdb` (reads NDJSON via `DBT_TRADES_GLOB`) | `athena` (reads the `bronze_trades` Glue table, writes Iceberg) |
| transform cadence | `refresh_marts_loop.py` (local loop) | Stage-6 EventBridge schedule |

The consumer is unchanged because it derives its S3 endpoint from `AWS_ENDPOINT_URL` (`settings.aws_endpoint_url()`), which LocalStack injects into the Lambda automatically and AWS leaves unset.

## Key decisions

- **NDJSON + boto3-only consumer.** No Parquet libraries in the hot path → no Lambda layer → the *same* consumer runs on LocalStack and AWS. Silver does the typing; the cost is a JSON-SerDe Athena table instead of Parquet (fine at streaming volumes).
- **Idempotent by construction.** The S3 key is the batch's **last Kinesis sequence number**, so a retried batch overwrites the same file; Silver then dedups by the natural key. Re-runs never duplicate.
- **Natural key = `(product_id, trade_id)`.** Coinbase `trade_id` is a **per-product** sequence, not globally unique — deduping on `trade_id` alone would silently drop a real trade *and* a global `unique` test would mask it. (Caught by the pre-PR review; see below.)
- **Provisioned 1-shard, gated off.** At 1 shard, provisioned (`$0.015/shard-hr`) beats on-demand (`$0.08/shard-hr`); `enable_streaming` (default `false`) keeps the whole stack at **$0** until a deliberate test window.
- **`LATEST` starting position.** The stream is created in the same apply, so there are no pre-existing records to miss.
- **Build the Lambda package before every deploy.** `build_lambda.sh` stages `infra/build/lambda` from the current `src/`. A package built before the streaming code existed deploys a consumer that can't import its module (`Runtime.ImportModuleError` → every ESM invoke dead-letters). Added the missing `src/ingestion/__init__.py` so it's an explicit regular package (not a fragile namespace package) for both batch and streaming.
- **The native ESM drives the consumer on LocalStack too.** An earlier "ESM not firing locally" symptom was actually that stale-package `ImportError` (the ESM *was* invoking; the consumer crashed on import → DLQ). Once the package was fixed, the ESM auto-fires — so there is **no LocalStack-specific consumer path**; the same code runs on LocalStack and AWS.
- **Continuous mode is config-only.** `STREAM_WINDOW_SECONDS=0` streams until Ctrl+C; the bounded default (60) stays the AWS shape (never leave a real stream idle). The continuous Silver/Gold refresh is a generic `dbt` loop locally and the Stage-6 EventBridge schedule on AWS.

## The Kinesis account-block → future scope

The live `terraform apply -var enable_streaming=true` created **5 of 8** resources cleanly (consumer Lambda + log group, SQS DLQ, IAM role, `bronze_trades` table), then **`Kinesis: CreateStream` returned `SubscriptionRequiredException: The AWS Access Key Id needs a subscription for the service`** — an **account-level** restriction on the new AWS **Free Plan** (the same class as the Stage 3 Glue-ETL block; Kinesis itself is not subscribable on this tier). Resolution, mirroring Stage 3:
- The streaming IaC + code are **kept** (`streaming.tf`, gated `enable_streaming=false`); the partial deploy was **torn down** (`~$0` — the stream never existed, the rest was free-tier/idle for minutes).
- The **one live AWS data-flow run is future scope** — run on an unrestricted/paid account. Everything is **fully validated on LocalStack**, which has no such tier limit.

**Free-Plan-restricted services** (account tier, both hit during the build): **Kinesis Data Streams** and **Glue ETL jobs**. Everything core works — S3, Lambda, SQS, KMS, Athena, the **Glue Data Catalog** (only ETL *jobs* are blocked, not catalog tables), EventBridge, Secrets Manager.

## Reviews — multi-agent, adversarially verified (all **GO**)

- **Core streaming review** — *killed a false-positive* (the CMK DLQ needs **no** SQS resource policy: a Kinesis **poll-based** ESM writes the DLQ via the **execution role**, which already holds `sqs:SendMessage` + `kms:GenerateDataKey/Decrypt`), and *caught a real bug* the reviewers missed first time: `silver_trades` must dedup on **`(product_id, trade_id)`**, not `trade_id` alone (Coinbase `trade_id` is per-product; a global `unique` test would mask the silent loss). Fixed + cross-product regression test.
- **Execution-readiness audit** — traced the local + AWS run paths, produced the runbook corrections (PowerShell vs bash, the switching table), and is where the **stale-package root cause** was nailed: the consumer's `Runtime.ImportModuleError` (every ESM invoke dead-lettering) was the deploy of a package built before the streaming code existed — fixed by `build_lambda.sh` + the new `src/ingestion/__init__.py`. The native ESM path then auto-fires; the misread "ESM doesn't work on LocalStack" was that crash all along.
- **Final pre-PR review** (cost · infra · correctness · docs) — **GO, no must-fix.** **$0 AWS delta** (all local tooling / package hygiene; no Terraform/IAM changed; `enable_streaming` gate intact). Correctness verified: the Ctrl+C flush is **single-shot** (no duplicate sends), reconnect preserves the in-flight batch, the bounded path is unchanged, the refresh loop survives a per-cycle dbt failure. Docs match the code command-for-command. Deferred lows below.

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
- **Continuous-refresh scaling** — `refresh_marts_loop.py` rebuilds `silver_trades`/`gold_latest_price` as full `table`s each cycle (re-reads all accumulated Bronze). Fine for a demo; for long continuous runs, switch Silver to an incremental Iceberg `MERGE` (like `silver_prices`) so cost/time stay flat.
- **KMS explicit key policy** + a runbook/Budget-Action teardown guard for the streaming window.
- **AWS Budgets / Budget Actions as IaC** — CLAUDE.md wants `$20/$50/$80` budgets + auto-stop actions, but there's no `aws_budgets_*` Terraform in the repo (pre-existing gap, not from this stage). The Free-Plan auto-stop-at-$0 mitigates it; add an `infra/budgets.tf` before any real-AWS Kinesis window is opened.
- **Refresh loop on Athena** — `refresh_marts_loop.py` re-scans all Bronze each cycle (full-table rebuild); on the `athena` target a long continuous run trips the 100 MB workgroup cap and the loop silently retries. Add a warn when `DBT_TARGET=athena` + low `REFRESH_SECONDS`; the real fix is the incremental Silver MERGE above. (Cannot occur on this account — Athena+continuous needs Kinesis, which is account-blocked.)
- **Producer `ws.close()` on Ctrl+C** — a `KeyboardInterrupt` in continuous mode bypasses `ws.close()` (the OS reclaims the socket on exit; the pending batch is still flushed). Optional `try/finally` hardening.

## Boundary note (Terraform vs dbt)

Like Stage 3, the **Silver/Gold tables are built by dbt at runtime** (data plane). Terraform builds the **streaming transport** — Kinesis, the consumer Lambda, the DLQ, the event-source mapping — and the `bronze_trades` **catalog** table (control plane). This is the same [§2 control-plane/data-plane boundary](../terraform-architecture.md#2-scope--what-terraform-does-and-does-not-do): Terraform builds the pipes; the consumer and dbt move the data.
