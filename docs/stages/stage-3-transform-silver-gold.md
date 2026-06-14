# Stage 3 — Transform to Silver + Gold

> **High-level summary.** Refine raw Bronze into clean **Silver** and business **Gold** tables as
> **Apache Iceberg**, with data-quality tests, built primarily with **SQL ELT (dbt)** and — as a
> learning parallel — a **Glue PySpark** job. Config-swappable: the same models run on **DuckDB**
> locally and **Athena** on AWS. Detail lives in the code + [terraform-architecture.md](../terraform-architecture.md);
> this is the what/why.

| | |
| --- | --- |
| **Goal** | Bronze → Silver (typed, deduped, validated) → Gold (daily OHLC, moving avg, volatility, market movers), all Iceberg, DQ-gated. |
| **Status** | ✅ **SQL/Iceberg path DONE & live on AWS** (Athena). PySpark job **written + reviewed**; its one live run is **account-blocked** (→ future scope). |
| **Primary path** | dbt (`dbt-core` + `dbt-duckdb` / `dbt-athena`) — serverless, cheap. |
| **Cost** | Athena ≈ pennies (KB–MB data, 100 MB scan cap); Glue job $0 (never ran on this account). |

## What was built

**dbt project** (`src/transform/dbt/`) — one set of models, two engines by `--target`:

| Model | Layer | What it is |
| --- | --- | --- |
| `silver_prices` | Silver | typed (ISO strings → timestamps), **deduped** to one row per natural key (`coin_id`+`snapshot_date`+`snapshot_hour`), validated |
| `gold_daily_ohlc` | Gold | daily OHLC per coin from the hourly snapshot series; volume = day's *closing* 24h figure (not a sum) |
| `gold_price_moving_avg` | Gold | 7-day simple moving average of daily close |
| `gold_volatility` | Gold | rolling 7-day stddev of daily returns |
| `gold_market_movers` | Gold | latest snapshot per coin, ranked by 24h change |

**Data-quality gate** — `dbt_utils` + built-in tests (not-null keys/timestamps, unique natural key, `price > 0`, OHLC inequalities `high≥low` etc., row-count sanity vs Bronze and across Gold). `dbt build` runs models **and** tests; a failing test stops the downstream build.

**Materialization** — Silver is `incremental` + Iceberg **`MERGE`** on the natural key (re-runs upsert, no dupes); Gold marts are full-rebuild `table`. The date-series tables are **partitioned by `snapshot_date`** (Athena prunes scans → cost lever).

**Glue PySpark job** (`src/transform/glue_spark/bronze_to_silver.py` + `infra/transform.tf`) — reproduces Bronze→Silver in Spark DataFrames, writing a *separate* `silver_prices_spark` Iceberg table. A learning parallel; the SQL path is primary.

## The config-swap (how local ↔ AWS works)

The **same model SQL** runs on both engines, selected by dbt **target** (env-driven, no code change):

| Target | Engine | Reads Bronze from | Writes | Cost |
| --- | --- | --- | --- | --- |
| `duckdb` (default) | DuckDB | LocalStack S3 via `read_parquet` | local DuckDB tables | $0 |
| `duckdb_s3` | DuckDB | **real** S3 via `read_parquet` | local DuckDB tables | ~$0 |
| `athena` | Athena (Trino) | Glue `bronze_prices` table | **Iceberg** tables | pennies |

Engine dialect differences live in **one** place — `adapter.dispatch` macros (`parse_iso_timestamp`), never inline per-target `if` branches. DuckDB is the free twin of Athena, exactly as in Stage 2.

## Runbook — running it (local · AWS · the switch)

> The **same models** run everywhere; only the dbt **`--target`** and a few env vars change. (Canonical short version also in [`src/transform/dbt/README.md`](../../src/transform/dbt/README.md).)

**Offline tests — no infra, $0:**
```powershell
make test     # pytest: Silver dedup/typing + Gold OHLC logic on tiny fixtures (DuckDB, in-process)
```

**Local — DuckDB over LocalStack (the free twin).** Prereqs: Docker running, LocalStack up, Bronze landed in LocalStack S3.
```powershell
# 1. LocalStack up  (health: (Invoke-RestMethod http://localhost:4566/_localstack/health).services.s3 -> available)
docker compose -f infrastructure/localstack/docker-compose.yml up -d

# 2. Local env — test/test creds make DuckDB's S3 secret resolve AND reach LocalStack
$env:APP_ENV="local"; $env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"
$env:AWS_DEFAULT_REGION="us-east-1"; $env:AWS_ENDPOINT_URL="http://localhost:4566"
$env:BRONZE_BUCKET="marketpulse-local-bronze"; $env:MARKETDATA_API_KEY="<coingecko-key>"

# 3. Create the bucket in LocalStack + land top-N Bronze (no AWS CLI needed)
python -c "import boto3; boto3.client('s3',endpoint_url='http://localhost:4566').create_bucket(Bucket='marketpulse-local-bronze')"
python -m src.ingestion.batch.ingest

# 4. Build + test Silver/Gold on DuckDB over LocalStack
$env:DBT_BRONZE_GLOB="s3://$($env:BRONZE_BUCKET)/prices/**/*.parquet"; $env:DBT_S3_ENDPOINT="localhost:4566"
cd src/transform/dbt; dbt deps; dbt build --profiles-dir . --project-dir .; cd ../../..
```
Tables land in `src/transform/dbt/target/marketpulse.duckdb` (inspect: `python -c "import duckdb; print(duckdb.connect('src/transform/dbt/target/marketpulse.duckdb',read_only=True).sql('select count(*) from silver_prices').fetchall())"`).

**Local — DuckDB over the REAL S3 Bronze (target `duckdb_s3`, no Docker):**
```powershell
$env:AWS_PROFILE="marketpulse-admin"
$bronze=(terraform -chdir=infra output -json bucket_names | ConvertFrom-Json).bronze
$env:DBT_BRONZE_GLOB="s3://$bronze/prices/**/*.parquet"
cd src/transform/dbt; dbt build --target duckdb_s3 --profiles-dir . --project-dir .; cd ../../..
```

**AWS live — Athena writes Iceberg (target `athena`):**
```powershell
$env:AWS_PROFILE="marketpulse-admin"
$names=(terraform -chdir=infra output -json bucket_names | ConvertFrom-Json); $res=terraform -chdir=infra output -raw athena_results_bucket
$env:DBT_SILVER_DATA="s3://$($names.silver)/"; $env:DBT_GOLD_DATA="s3://$($names.gold)/"
$env:DBT_ATHENA_STAGING="s3://$res/query-results/"; $env:AWS_DEFAULT_REGION="us-east-1"
cd src/transform/dbt
dbt build --target athena --full-refresh    # first run / after switching Silver to incremental
dbt build --target athena                   # thereafter: Silver does an incremental MERGE
cd ../../..
```
Verify (Athena query editor, workgroup `marketpulse-dev-athena-analytics`): `SELECT count(*) FROM silver_prices;` — or `dbt show --select gold_market_movers --limit 10 --target athena --profiles-dir src/transform/dbt --project-dir src/transform/dbt`.

> **Backfilling an older date:** the Silver incremental only moves **forward** (reprocesses `snapshot_date >=` Silver's current max). If you re-ingest or backfill a date **older** than that max, a plain incremental run won't pick it up (and `assert_silver_rowcount_matches_bronze` will then fail). Recover with a one-off **`dbt build --full-refresh --select silver_prices+`** (rebuilds Silver + its downstream Gold from all Bronze). Normal forward hourly ingestion never hits this.

**The switch — what actually changes (same model SQL throughout):**

| | local default | local real-S3 | AWS live |
| --- | --- | --- | --- |
| `--target` | `duckdb` | `duckdb_s3` | `athena` |
| reads Bronze | LocalStack S3 (`read_parquet`) | real S3 (`read_parquet`) | Glue `bronze_prices` |
| writes to | local `.duckdb` file | local `.duckdb` file | **Iceberg** in `marketpulse_dev` (S3) |
| creds | `test/test` + endpoint | `AWS_PROFILE` | `AWS_PROFILE` |

Dialect gaps live only in the `adapter.dispatch` macros. The `athena` target **fails fast** (an `on-run-start` guard) if `DBT_SILVER_DATA` / `DBT_GOLD_DATA` / `DBT_ATHENA_STAGING` are unset — so Iceberg data can never land in the wrong (transient) bucket.

**Glue Spark job (account-blocked here — the commands for an unrestricted account):**
```powershell
$env:AWS_PROFILE="marketpulse-admin"
terraform -chdir=infra apply -var enable_glue_spark=true     # deploy job + role + script
$job=terraform -chdir=infra output -raw glue_bronze_to_silver_job
python -c "import boto3,sys; print(boto3.client('glue').start_job_run(JobName=sys.argv[1])['JobRunId'])" $job
# On THIS account: Glue:CreateJob -> AccessDenied (Free Plan). Elsewhere it writes silver_prices_spark;
# verify parity:  SELECT count(*) FROM silver_prices_spark;  == SELECT count(*) FROM silver_prices;
terraform -chdir=infra apply -var enable_glue_spark=false    # teardown ($0 idle anyway)
```

## Key decisions

- **Gold from hourly snapshots, not ticks.** Bronze is hourly point-in-time snapshots, so OHLC is derived from the day's `current_price` series; `total_volume`/`high_24h`/`low_24h` are 24h-rolling API figures and are **never summed**.
- **table-first → then MERGE.** Proved cross-engine parity with simple full-rebuild tables, then converted Silver to incremental Iceberg MERGE for production idempotency.
- **Spark path = Glue-only.** No local Java/JVM (+ Windows Spark friction, ~6 days of credits, Stages 4–8 ahead) → skip local Spark for now; validate on one live Glue run. See *Deferred*.

## Cross-engine gotchas learned (DuckDB passes ≠ Athena passes)

The local DuckDB build passing does **not** guarantee Athena validity — only a real Athena run does. Hit/fixed during 3c:

1. **No `QUALIFY` on Athena engine v3** → portable subquery + `WHERE row_number()=1`.
2. **Athena CTAS can't persist `timestamp with time zone`** → normalize to tz-naive UTC `timestamp` in the dispatch macro.
3. **NULLS ordering differs** (Trino `DESC` = NULLS FIRST; DuckDB = NULLS LAST) → always `nulls last` + a deterministic tiebreaker (the `gold_market_movers` rank).

## The Glue Spark run — account-blocked → future scope

`terraform apply -var enable_glue_spark=true` created the role + policy + script, but **`Glue: CreateJob` returned `AccessDeniedException: Account … is denied access`** — an **account-level** restriction (the message says *Account*, not *User not authorized*), consistent with the new AWS **Free Plan** limiting Glue ETL jobs. The same `marketpulse-admin` can use the Glue *catalog* (dbt creates tables fine) — only ETL *jobs* are blocked. Resolution:
- The job + least-privilege IaC are **kept** (`transform.tf`, gated `enable_glue_spark=false`); the 3 partial resources were torn down.
- The one live run is **future scope** — run on an unrestricted/paid account, alongside the local-Spark setup.

## Definition of done

| DoD item | Status |
| --- | --- |
| Gold Iceberg tables exist (SQL/dbt path) | ✅ live on AWS |
| dbt tests pass | ✅ on DuckDB **and** Athena |
| Athena query on a Gold mart returns sensible numbers | ✅ (`gold_market_movers` via `dbt show`) |
| PySpark job reproduces Bronze→Silver | ⏳ written + reviewed; live run account-blocked → future scope |
| Partitioning strategy | ✅ Iceberg partitioned by date |

## Deferred / future scope

- **Local Java + Spark** — run the PySpark path locally as a true twin of Glue (optional learning enhancement).
- **Live Glue run** — execute `bronze_to_silver.py` on Glue once on an account where Glue ETL is permitted; document Spark-vs-SQL cost.

## Boundary note (Terraform vs dbt)

Stage 3's core tables (Silver/Gold Iceberg) are created by **dbt at runtime** (data plane) — *not* Terraform — exactly like the Lambda writes Bronze data. Terraform only adds the optional **Glue job** (control plane). This reinforces the [§2 control-plane/data-plane boundary](../terraform-architecture.md#2-scope--what-terraform-does-and-does-not-do).
