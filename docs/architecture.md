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
                          RAG assistant (multi-provider LLM) + FAISS-in-S3
```
- **Bronze** raw/append-only · **Silver** clean/typed · **Gold** business-ready marts.
- Partitioned by date; file sizes kept reasonable (Iceberg compaction) — the main control on Athena cost.

## Portability — cloud ↔ local by config
Ports-and-adapters (hexagonal): a `QueryEngine` port (`AthenaBackend` / `DuckDBBackend`) in
`config/settings.py`, and an **LLM handler** port owned by the standalone `rag/` project
(`rag/app/llm/`) — a **config-driven multi-provider** layer (Ollama, Gemini, OpenAI, Anthropic,
Groq, Bedrock) selected in `rag/config/llm.yaml`, with separate embed/generate providers + fallback.
Group-A AWS services
switch via `AWS_ENDPOINT_URL` (LocalStack) with no adapter. Switching environments = config change
only. The `rag/` package shares exactly one seam with the core — `settings.get_gold_query_engine()`
(Athena on AWS / DuckDB over the dbt Gold DB locally) — so it can be lifted into its own repo.

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
- **Streaming (Stage 4):** Kinesis (1-shard provisioned, gated off when idle) → consumer Lambda → **NDJSON** Bronze → dbt `silver_trades`/`gold_latest_price`, with an SQS DLQ. The consumer is **boto3-only (no Lambda layer)** so it runs unchanged on LocalStack and AWS. (Live Kinesis is account-blocked on the Free Plan → validated on LocalStack.)
- **FAISS-in-S3** for vectors (not OpenSearch) — near-zero cost.
- **RAG assistant (Stage 5):** a standalone, repo-extractable `rag/` project — idempotent RSS doc ingestion → chunk → embed → **FAISS-in-S3** (`gold/rag/<env>/`), and a **hybrid** assistant that retrieves chunks **and** runs a templated Gold query (`gold_market_movers`/`gold_latest_price`) for live numbers, composed by the LLM. Exposed as a **FastAPI** service (SSE streaming) + a **Streamlit** chat UI + a CLI; three least-privilege Lambdas are the AWS deploy shape. Runs locally at $0 (Ollama + DuckDB); AWS apply is gated off (Bedrock access + a faiss/numpy Lambda layer are deferred).
- **LLM = config-driven multi-provider** (`rag/config/llm.yaml`): Ollama (Cloud by default, or local via `OLLAMA_HOST`) / Gemini / OpenAI / Anthropic / Groq / Bedrock, with separate embed vs generate providers + an optional **generation** fallback (embeddings have none — a different embedder is an incompatible vector space); keys in `.env`, switchable via `RAG_GEN_PROVIDER`. The `LLMHandler` port keeps every provider behind one interface.
- **AWS region:** `us-east-1` (widest Bedrock availability, lowest reference pricing, local parity).
- **Secret scanner:** `detect-secrets` (Python-native, baseline-driven).

## Diagram
_TODO (Stage 8): Mermaid/diagram-tool rendering of the flow above._
