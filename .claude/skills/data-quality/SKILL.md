---
name: data-quality
description: Data-quality and pipeline-correctness rules for the MarketPulse lakehouse. Use when building or editing ingestion, transforms, dbt models, Spark/Glue jobs, or any data tests across the Bronze/Silver/Gold layers.
---

# Data quality & pipeline correctness (MarketPulse)

## Medallion layers
- **Bronze** = raw, as-ingested, immutable.
- **Silver** = cleaned, typed, deduplicated.
- **Gold** = business aggregates / serving tables.
- Each layer must be **reproducible from the layer below**.

## Idempotency (re-runs must be safe)
- Every pipeline run must be safe to re-run **without duplicating data**.
- Use **partition overwrite** or **MERGE/upsert** (Iceberg) — never a blind append that double-counts.
- Use deterministic partition keys (e.g. event date).

## Quality checks — fail the pipeline on violation
- No **null** primary keys or event timestamps.
- Values within sane ranges (e.g. `price > 0`, non-negative volume).
- **Row-count sanity** vs the source (catch silent data loss).
- **Uniqueness** of the natural key in Silver.

## dbt tests
- Use built-in tests on key columns: `not_null`, `unique`, `accepted_values`, `relationships`.
- Add custom tests for business rules.
- Tests must **pass before promoting** data downstream.

## Quality gate
- In the Step Functions pipeline, run checks **between Silver and Gold**. On failure, **stop and alert** — do not publish bad data to Gold.

## Performance & cost (ties to the $99 budget)
- Write **Parquet**, **partition by date**, keep file sizes reasonable so Athena scans little data.

## Engineering standards
- Write the **critical data tests alongside the transform**, and run them locally (**DuckDB**) before deploying.
- Log **row counts in/out** and check results per run (critical events) — not noisy per-row logs.
