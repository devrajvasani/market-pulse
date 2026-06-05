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
