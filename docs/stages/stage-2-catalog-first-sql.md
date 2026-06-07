# Stage 2 — Catalog + First SQL

**Status:** Done (2026-06-07) · Spec: `docs/plan/02_PROJECT_PLAN_IN_DEPTH.md` (Stage 2)

**Goal:** make the lakehouse **"see"** the Bronze Parquet (a Glue Data Catalog table) and run the **first SQL query** over it (Athena) — with the **same query runnable cloud ↔ local by config** (Athena ↔ DuckDB), proving the data is queryable before Stage 3 transforms.

## What was built (in repo)
- **Terraform — 2 new modules + composition** ([catalog.tf](../../infra/catalog.tf)):
  - **`glue_catalog`** — `aws_glue_catalog_database` (`marketpulse_dev`) + `aws_glue_catalog_table` (`bronze_prices`): the 24 data columns + 2 partition keys, **partition projection** on `snapshot_date`/`snapshot_hour` (no crawler), Parquet SerDe, pointing at `…/bronze/prices/`.
  - **`athena`** — `aws_athena_workgroup` with a **100 MB `bytes_scanned_cutoff_per_query`** (the cost guardrail), `enforce_workgroup_configuration`, SSE-KMS results.
  - **`athena_results`** bucket — a dedicated, encrypted results bucket (reuses the `s3` module).
  - All three **gated by `enable_catalog`** (true on AWS, false on LocalStack), with `moved` blocks so the gate migrates state cleanly (no recreate).
- **QueryEngine adapters (`src/backends/`)** — the config-swap made real:
  - `AthenaBackend.run_sql` — `awswrangler.athena.read_sql_query` (`ctas_approach=False`) scoped to the workgroup + database.
  - `DuckDBBackend.run_sql` — `read_parquet` over the Bronze prefix (`hive_partitioning`, partition cols kept as strings), registering a `bronze_prices` view — **no catalog needed**; works against real S3 or LocalStack S3.
  - `src/query/run.py` runner (`make query`, `--engine athena|duckdb`); `config/settings.py` gained `athena_database()`, `athena_workgroup()`, `bronze_data_location()`.
- **Tests** — 33 total (added: Athena/DuckDB settings, DuckDB local-Parquet count + string partition cols + hourly accumulation, Athena mocked).

## The concept (schema-on-read)
- A **Glue table is metadata only** — it *describes* the Parquet already in S3 (columns, types, location, partitions) and stores **zero rows**. **Athena needs it**; **DuckDB does not** (it reads the files directly).
- **Partition projection** computes `snapshot_date`/`snapshot_hour` from the object path → **no crawler ($0), zero-maintenance**.
- **Athena workgroup** = serverless SQL ($5/TB scanned) **hard-capped at 100 MB/query**; results land SSE-KMS-encrypted in `athena-results`.

---

## Local ↔ Live AWS — running Stage 2 (core concept)

The defining twist vs. Stage 1: **Glue + Athena are LocalStack *Pro*** — they are **not** in the free Community edition we use. So Stage 2's *catalog* can't run locally at all. The project's answer is the **DuckDB twin** (which needs no catalog) plus an **`enable_catalog` gate** that skips Glue/Athena when building into LocalStack.

### The two rails (same as Stage 1, plus the query engine)

| Layer | What it does | **Local** | **Live AWS** |
|---|---|---|---|
| **Infra** (Terraform) | builds the catalog/workgroup | `tflocal apply -var="enable_catalog=false"` → **skips** Glue/Athena | `terraform apply` → **builds** Glue + Athena |
| **Runtime** (query) | runs the SQL | `APP_ENV=local` → **DuckDB** (`DuckDBBackend`) | `APP_ENV=aws` → **Athena** (`AthenaBackend`) |
| Data | where the Parquet lives | LocalStack S3 | real S3 Bronze |
| Catalog | how the table is "seen" | **none** (DuckDB reads files) | **Glue** `bronze_prices` |
| Cost | | $0 | ~$0 (KB scans, 100 MB cap) |
| The SQL | | `SELECT count(*) FROM bronze_prices` | *(identical)* |

```
        IDENTICAL ARTIFACTS                         TWO TARGETS  (pick by config)
   ┌──────────────────────────┐
   │  infra/*.tf  (Terraform) │      ┌──────────── LOCAL · LocalStack · $0 ─────────────┐
   │  src/query (Python)      │ ───► │ tflocal apply -var enable_catalog=false           │
   └──────────────────────────┘      │   → S3 + Stage 1 only (NO Glue/Athena)            │
              │                       │ APP_ENV=local → DuckDBBackend                     │
              │                       │   read_parquet(s3://…@localhost:4566) → no catalog│
              │                       │ state: LOCAL file (backend-override) · creds test │
              │                       └───────────────────────────────────────────────────┘
              │                       ┌──────────── LIVE · real AWS ─────────────────────┐
              └─────────────────────► │ terraform apply → Glue db + table + Athena wg     │
                                      │ APP_ENV=aws → AthenaBackend → Athena (Glue table) │
                                      │ state: S3 backend · creds ~/.aws (marketpulse-admin)│
                                      └───────────────────────────────────────────────────┘
```

### ⚠️ Two LocalStack-only gotchas (learned the hard way)
1. **`tflocal` does NOT redirect the committed `backend "s3"` to LocalStack** — it reads the **real AWS state**. Refreshing real resources against LocalStack then throws Glue/Athena `501`s and a plan to "recreate everything" (a dangerous mismatch). **Fix:** force a **local state file** with a gitignored `infra/localstack_backend_override.tf` containing `terraform { backend "local" {} }`. Then it physically cannot touch the cloud state. *(Memory: `local-tf-needs-local-backend-override`.)*
2. **PowerShell mangles `-backend-config=backend.hcl`** → "Too many command line arguments". **Quote it:** `"-backend-config=backend.hcl"`.

> 🛡️ **Always plan-first** before a local `apply`: abort if the plan shows **real ARNs** (`…:724166961779:…`) or `# … has been deleted` — that means it's reading the real state, not a fresh local one.

---

## The complete procedure (exactly as performed in Stage 2)

### A · Run on LIVE AWS — deploy the catalog, then query both engines
```powershell
$env:AWS_PROFILE = "marketpulse-admin"

# 1. DEPLOY (catalog ON by default). cost + infra review pass first; I run plan, you run apply.
terraform -chdir=infra plan                 # review: 7 to add (Glue db+table, Athena wg, results bucket)
terraform -chdir=infra apply                # YOU run -> "7 added, 0 changed, 0 destroyed"

# 2. QUERY — same SQL, two engines, same answer
$env:APP_ENV = "aws"
$env:BRONZE_BUCKET = "marketpulse-dev-bucket-bronze-65fa4d26"
uv run python -m src.query.run --engine athena   # Athena via the Glue catalog -> {'row_count': 76}
uv run python -m src.query.run --engine duckdb   # DuckDB over the SAME real S3 -> {'row_count': 76}
```
You can also query in the **Athena console**: Query editor → Workgroup `marketpulse-dev-athena-analytics` → Database `marketpulse_dev` → `SELECT count(*) FROM bronze_prices;`.

### B · Run LOCALLY — LocalStack S3 + DuckDB (free, isolated)
```powershell
# 1. ENABLE LocalStack
docker compose -f infrastructure/localstack/docker-compose.yml up -d
docker compose -f infrastructure/localstack/docker-compose.yml ps   # wait: localstack = "healthy"

# 2. FORCE LOCAL STATE (gitignored override) so tflocal can't touch the real AWS state
'terraform { backend "local" {} }' | Set-Content infra/localstack_backend_override.tf

cd infra
uv run tflocal init -reconfigure                                    # -> backend "local"
uv run tflocal plan  -var="enable_catalog=false"                   # 🛡️ verify: + create only, NO real ARNs
uv run tflocal apply -var="enable_catalog=false" -var="awswrangler_layer_arn="   # 23 added (NO Glue/Athena)

# 3. INGEST into LocalStack S3, then QUERY with the DuckDB twin
$env:BRONZE_BUCKET = (uv run tflocal output -json bucket_names | ConvertFrom-Json).bronze
$env:APP_ENV = "local"
cd ..
uv run python -m src.ingestion.batch.ingest                        # CoinGecko (real) -> LocalStack S3
uv run python -m src.query.run --engine duckdb                     # DuckDB over LocalStack S3 -> {'row_count': 2}
```
- `-var="enable_catalog=false"` = the gate skips Glue/Athena (LocalStack Pro).
- `-var="awswrangler_layer_arn="` = no cloud-only Lambda layer locally.
- `APP_ENV=local` loads `config/environments/local.env` (`AWS_ENDPOINT_URL=http://localhost:4566`, dummy `test/test`), so both ingestion and DuckDB hit LocalStack.

### C · Switch back to AWS (and the state-separation rule)
```powershell
cd infra; uv run tflocal destroy -var="enable_catalog=false" -var="awswrangler_layer_arn="; cd ..
docker compose -f infrastructure/localstack/docker-compose.yml down
Remove-Item infra/localstack_backend_override.tf                        # drop the local-state override
$env:AWS_PROFILE = "marketpulse-admin"
terraform -chdir=infra init -reconfigure "-backend-config=backend.hcl"  # re-point at LIVE AWS (quoted!)
terraform -chdir=infra plan                                            # expect: 0 to add/change/destroy
```
> **Rule:** *local = local-file state (via the override), AWS = S3 backend; never share one state.* While the override file exists, **all** `terraform`/`tflocal` in `infra/` use local state — removing it + re-init returns you to the cloud. (If `init` errors with *"No valid credential sources"*, you just forgot `$env:AWS_PROFILE`.)

### When to use which
| Use **Local (DuckDB)** when… | Use **Live AWS (Athena)** when… |
|---|---|
| iterating on SQL/transform logic, learning, $0 | you need the real Glue catalog + Athena (the managed query path) |
| you want instant, offline feedback | verifying the live pipeline end-to-end (Stage 1 → catalog → query) |
| `tflocal apply` + `--engine duckdb` | `terraform apply` (human-gated) + `--engine athena` |

---

## Querying — one runner, two engines
`config/settings.get_query_engine()` picks the engine by `APP_ENV`; `src/query/run.py` lets you force one:
```powershell
uv run python -m src.query.run                                  # default: APP_ENV picks the engine
uv run python -m src.query.run --engine athena "SELECT count(*) FROM bronze_prices"
uv run python -m src.query.run --engine duckdb "SELECT coin_id, current_price FROM bronze_prices"
make query                                                      # SELECT count(*) via the active engine
```
The SQL is **identical** across engines — DuckDB registers `bronze_prices` as a view over `read_parquet(...)`; Athena resolves it via the Glue table.

## Verified
- **Live AWS:** `terraform apply` → **7 added, 0 changed, 0 destroyed**. Athena = DuckDB = **76** over the same Bronze; the most-recent hourly partitions (07→11 UTC) confirm **Stage 1's Lambda is actively feeding Stage 2** (partition projection live; scans tiny).
- **Local:** `tflocal apply -var="enable_catalog=false"` (23 in LocalStack, no Glue/Athena) → ingest → LocalStack S3 (`…-bronze-4135b7c5`) → DuckDB = **2**. Fully isolated (different bucket suffix from AWS).
- `ruff` ✓ · **33 tests** ✓ · `terraform validate` ✓.

## Pre-deploy reviews
- **cost-reviewer:** GO — **~$0** (Glue Catalog free tier; manual table = no crawler; Athena KB scans, 100 MB cap; results bucket KB).
- **infra-reviewer:** raised 2 "blockers" — **both disproven by the Console-first walkthrough**: (1) the `marketpulse-admin` user *did* write SSE-KMS Athena results (KMS access fine); (2) integer-projection-on-string-partition returned **all** partitions (count 26 across every hour). Applied: log the exception class in both adapters. **Deferred:** Glue *catalog-metadata* encryption (account-wide; metadata is non-sensitive field names); the `athena-results` lifecycle rule.

## Key choices
- **Manual table + partition projection** (no crawler) — $0, deterministic, zero-maintenance.
- **100 MB scan cap** on the workgroup — the cost guardrail.
- **Dedicated `athena-results` bucket** — encrypted, separate from lake data.
- **Both QueryEngine adapters wired** — same SQL on Athena (cloud) and DuckDB (local).
- **`enable_catalog` gate + local-backend override** — one Terraform, clean local/AWS separation.

## Left to do (deferred)
- **S3 noncurrent-version lifecycle rule** (esp. `athena-results`) — before scaling / widening coins.
- **Glue catalog-metadata encryption** (`aws_glue_data_catalog_encryption_settings`) — optional, account-wide.
- **Scoped Athena query IAM role** — Stage 5 (the assistant Lambda); Stage 2 queries run as the `marketpulse-admin` user.
