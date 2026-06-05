# MarketPulse — Architecture & Key Decisions

> Seeded in Stage 0; the diagram and full decision log are finalised in Stage 8.
> High-level overview: `docs/plan/01_PROJECT_PLAN_HIGH_LEVEL.md`.
> Infrastructure-as-Code deep-dive (modules, wiring, diagrams): [terraform-architecture.md](terraform-architecture.md).

## Data flow (medallion lakehouse)
```
ingest (batch + streaming + docs) ─► S3 BRONZE (raw, Parquet, partitioned by date)
                                       │  transforms (SQL ELT primary; PySpark learning path)
                                       ▼
                                     S3 SILVER (typed, de-duplicated, validated — Iceberg)
                                       ▼
                                     S3 GOLD (business marts — Iceberg)
                                       ▼
                          query: Athena (cloud) / DuckDB (local)
                          RAG assistant (Bedrock / Ollama) + FAISS-in-S3
```
- **Bronze** raw/append-only · **Silver** clean/typed · **Gold** business-ready marts.
- Partitioned by date; file sizes kept reasonable (Iceberg compaction) — the main control on Athena cost.

## Portability — cloud ↔ local by config
Ports-and-adapters (hexagonal): a `QueryEngine` port (`AthenaBackend` / `DuckDBBackend`)
and an `LLMClient` port (`BedrockBackend` / `OllamaBackend`), selected by `APP_ENV` in
`config/settings.py`. Group-A AWS services switch via `AWS_ENDPOINT_URL` (LocalStack) with
no adapter. Switching environments = config change only.

## Idempotency design (safe re-runs)
The design intent — re-running any step produces the same result, never duplicates:
- **Ingestion:** deterministic keys; overwrite the date partition rather than blind append.
- **Transforms:** Iceberg `MERGE` / de-duplication in Silver.
- **Infra:** Terraform is idempotent; never hand-create a resource that Terraform also manages.
- **Files/objects:** check-before-create; create, skip, or overwrite deliberately.

_(Per-component specifics are filled in as each stage lands; full write-up in Stage 8.)_

## Key decisions (log; appended each stage)
- **$99 hard budget** → serverless-first; avoid MWAA/Redshift/MSK/OpenSearch/NAT.
- **SQL ELT (Athena/dbt) is the primary transform path**; Glue PySpark is a parallel learning path.
- **FAISS-in-S3** for vectors (not OpenSearch) — near-zero cost.
- **AWS region:** `us-east-1` (widest Bedrock availability, lowest reference pricing, local parity).
- **Secret scanner:** `detect-secrets` (Python-native, baseline-driven).

## Diagram
_TODO (Stage 8): Mermaid/diagram-tool rendering of the flow above._
