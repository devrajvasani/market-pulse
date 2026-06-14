# MarketPulse — dbt transforms (Bronze → Silver → Gold)

SQL-based ELT that refines the raw Bronze lake into clean **Silver** and
business-ready **Gold** tables, with data-quality tests that fail the build on
bad data. The *same* models run on two engines, selected by dbt **target**:

| Target               | Engine              | Reads Bronze from                       | Writes                   | Cost     |
| -------------------- | ------------------- | --------------------------------------- | ------------------------ | -------- |
| `duckdb` (default) | DuckDB (local twin) | **LocalStack** S3 via `read_parquet` (endpoint forced) | local DuckDB tables      | $0       |
| `duckdb_s3`        | DuckDB (local twin) | **real** AWS S3 via `read_parquet` (credential chain, no LocalStack) | local DuckDB tables      | ~$0      |
| `athena` (3c)      | Athena (Trino)      | Glue table `bronze_prices`            | **Iceberg** tables | ~pennies |

Engine dialect differences are isolated in `macros/` via `adapter.dispatch`
(e.g. `parse_iso_timestamp`) — never inline per-target `if` branches.

## Layout

```
dbt_project.yml          project config (profile, paths, materializations)
profiles.yml             connection targets (no secrets; env-driven)
packages.yml             dbt_utils (standard cross-adapter data tests)
macros/                  adapter.dispatch dialect bridges
models/_sources.yml      Bronze source (Glue table on Athena; read_parquet on DuckDB)
models/silver/           silver_prices.sql + its data-quality tests
models/gold/             daily_ohlc, price_moving_avg, volatility, market_movers + tests
tests/                   singular data tests (e.g. row-count sanity vs Bronze / Gold)
```

## Run locally (free, LocalStack)

Prereqs: `make install`, LocalStack up with Bronze data, and `BRONZE_BUCKET` set
to the LocalStack bronze bucket (see repo `DEVELOPER_GUIDE`). Then:

```bash
make transform-local        # dbt deps + dbt build (runs models AND tests) on DuckDB
```

`make test` (repo root) runs the fast, offline unit test of the transform logic —
no LocalStack, no network — and is part of the pre-commit / CI gate.

## Run on AWS (3c — writes Iceberg)

Needs real AWS credentials. The *same* models + tests run on Athena, writing Silver/Gold
as **Iceberg** tables into the existing `marketpulse_dev` Glue database — table data in the
durable `silver`/`gold` buckets (`s3_data_dir`), query results in the athena-results bucket
(`s3_staging_dir`). Every query is governed by the Stage-2 workgroup (100 MB scan cap,
KMS-encrypted results). Set the locations from `terraform output`, then:

```bash
dbt build --target athena   # same models + tests, now on Athena
```
