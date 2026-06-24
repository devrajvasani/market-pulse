# Stage 5 — Hybrid RAG assistant (standalone FastAPI + Streamlit)

> **High-level summary.** Add a **hybrid RAG assistant** that answers plain-English market
> questions using **both** ingested crypto-news documents **and** live **Gold** metrics. Built as a
> **standalone, repo-extractable `rag/` project**: idempotent RSS ingestion → chunk → embed →
> **FAISS-in-S3**, plus an assistant that retrieves chunks **and** runs a **templated Gold query**
> for real numbers, composed by an LLM. Exposed as a production **FastAPI** service (SSE streaming) +
> a polished **Streamlit** chat UI + a CLI. Config-swappable: identical code runs **locally**
> (Ollama + DuckDB) and on **AWS** (Bedrock + Athena); only `APP_ENV` changes. Detail lives in the
> code + [terraform-architecture.md](../terraform-architecture.md); this is the what/why.

| | |
| --- | --- |
| **Goal** | Ask *"What moved Bitcoin this week and by how much?"* → an answer that **cites a news chunk** *and* shows a **real Gold number**. (plan `02` §313, DoD §326.) |
| **Status** | ✅ **Built & validated locally** end-to-end (CLI + FastAPI + Streamlit; Ollama + DuckDB-over-the-dbt-Gold-DB + FAISS-in-LocalStack-S3). Cloud-agnostic — same code targets AWS. **AWS apply deferred** (`enable_rag=false`): Bedrock model access is uncertain on the Free Plan and a faiss/numpy Lambda layer is still needed; the IaC is written + `terraform validate`-clean. |
| **Primary path** | RSS → `bronze/docs/` (idempotent) → chunk → embed (LLM handler) → FAISS index + meta in `gold/rag/<env>/`; assistant = retrieve top-k **+** templated Gold query → LLM compose. |
| **Cost** | **$0 baseline** — the `rag/` app runs free locally; every AWS resource is gated by `enable_rag` (default `false`). If enabled: Lambda + Athena (100 MB scan cap) + Bedrock tokens + tiny S3 → well under $1 for a dev session. **No OpenSearch** (FAISS-in-S3), no new bucket (reuses gold), no always-on cost. |

## What was built — a standalone `rag/` project

A self-contained, layered package (so it can later be lifted into its own repo); the **only** seam
with the core is the Gold `QueryEngine`.

```
rag/
  app/
    core/      config.py (env config) · logging.py (JSON) · errors.py (RAGError hierarchy) · llm_config.py (llm.yaml loader/validator)
    llm/       base.py (LLMHandler port) · {ollama,gemini,openai,groq,anthropic,bedrock}_handler.py · composite.py (router) · registry.py · factory.py · _http.py · _openai_compat.py
    domain/    models.py (Document, Chunk)
    services/  ingestion · chunking · indexing · retrieval · gold · assistant   (framework-free)
    repositories/  doc_store (S3 bronze/docs) · index_store (S3 gold/rag) · gold_query (shared seam)
    schemas/   chat.py (pydantic DTOs)
    api/       v1/routes.py · deps.py · errors.py
    main.py    FastAPI factory (CORS, request-id, error handlers)
    cli.py     ingest / build-index / ask
  config/      llm.yaml (LLM provider registry — version-controlled, no secrets)
  ui/          streamlit_app.py + .streamlit/config.toml  (chat UI: streaming, citations, Gold metrics)
  lambda_handler.py   ingest / index / assistant (the AWS deploy shape)
  tests/       97 offline tests (mocked urlopen/boto3, moto S3, DuckDB fixtures, FastAPI TestClient)
```

| Piece | What it is |
| --- | --- |
| **LLM handlers** (`app/llm/`) | The `LLMHandler` port (`embed`/`generate`/`generate_stream`) with **6 providers**: `OllamaHandler` (urllib + bearer auth; Ollama Cloud by default, or local via `OLLAMA_HOST`), `GeminiHandler`/`OpenAIHandler`/`GroqHandler`/`AnthropicHandler` (httpx REST + shared `_http.py` retry/SSE; OpenAI + Groq share the `_openai_compat.py` base), and `BedrockHandler` (boto3, API-key bearer token). A **`CompositeLLM`** routes embeddings → the embed provider and generation → the gen provider, each with an **optional auto-fallback**. `factory.get_llm_handler()` builds it from the YAML registry. Anthropic + Groq are generation-only (no embeddings). |
| **Ingestion** (`services/ingestion.py`) | Fetches free crypto-news **RSS** (no API key) via `urllib`, parses RSS 2.0 / Atom with **`defusedxml`**, strips HTML, and writes one cleaned `Document` per article to `bronze/docs/source=<host>/<sha256(guid)>.json`. **Idempotent** (deterministic key) — re-ingesting never duplicates. |
| **Chunking** (`services/chunking.py`) | Deterministic char/overlap windows; `chunk_id = sha256(doc_id:index)` → re-chunking yields identical ids (no duplicate vectors). |
| **Indexing + retrieval** (`services/{indexing,retrieval}.py`, `vectorstore.py`) | Embeds chunks → builds a FAISS `IndexFlatIP` (cosine) → uploads `index.faiss` + `meta.json` to `gold/rag/<env>/` (env-namespaced: embedding dims differ per model). Retrieval loads/caches the index, embeds the query, returns top-k chunks with scores. |
| **Hybrid Gold** (`services/gold.py` + `repositories/gold_query.py`) | Intent router (whitelisted coin map + keywords) → **fixed templated SQL** over `gold_market_movers` (price + 1h/24h/7d moves + **all-time high** and % from ATH) and best-effort `gold_latest_price` (live streaming price). Runs through `settings.get_gold_query_engine()` → **Athena on AWS, DuckDB over the dbt Gold DB locally**. Values come only from the whitelist → no SQL-injection surface. |
| **Assistant** (`services/assistant.py`) | Orchestrates retrieve + Gold + LLM compose; a system prompt instructs the model to cite `[n]` sources and use the exact live numbers. `answer()` (blocking) + `answer_stream()` (typed `sources`/`delta`/`done` events). |
| **FastAPI** (`app/api/`, `app/main.py`) | `GET /health` `/ready`, `POST /ingest` `/index` `/ask` `/ask/stream` (**SSE**) — all under the `/api/v1` prefix. Global exception handlers map `RAGError`→JSON / 422 validation / catch-all 500 (no trace leak); request-id middleware; CORS (explicit origins — wildcard-with-credentials is refused). |
| **Streamlit UI** (`ui/streamlit_app.py`) | A clean chat (modelled on leading assistant UIs): streamed answers, example prompts, **citations** in an expander, live **Gold metrics** as `st.metric` cards, connection-aware sidebar. Talks to the API over HTTP (`RAG_API_URL`). |
| **Lambdas** (`lambda_handler.py` + `infra/rag.tf`) | Three handlers (ingest / index / assistant) + three least-privilege IAM roles, gated by `enable_rag`. The AWS deploy shape (see *Deferred*). |

**Seam changes in the core** — `config/settings.py`: added `get_gold_query_engine()` (Athena / DuckDB-on-dbt-DB) + `gold_bucket()`; **removed** the Stage-0 `get_llm_client()` and the `src/backends/{llm_client,bedrock,ollama}_backend.py` placeholders (the LLM port now lives in `rag/app/llm/`). `src/backends/duckdb_backend.py`: added a read-only **`database_path`** mode that opens the dbt Gold DuckDB file so Gold marts are queryable by name (the local twin of Athena-over-Glue).

## LLM providers (config-driven)

The LLM layer is a **multi-provider, config-driven** adapter. `rag/config/llm.yaml` (version-controlled, **no secrets**) declares the registry — Ollama, Gemini, OpenAI, Anthropic, Groq, Bedrock — plus which provider does **embeddings** vs **generation** and an optional **fallback** for each. API keys are referenced only by env-var *name* and read from `.env` (`GEMINI_API_KEY`, `OPENAI_API_KEY`, …); a `RAG_GEN_PROVIDER` / `RAG_EMBED_PROVIDER` env var overrides the active choice without editing the file. At startup the factory validates the selection (provider exists, key present, embed provider can actually embed) and **fails fast** with a clear message; it logs the chosen providers. Switching the *embed* provider changes the vector space, so the FAISS index is tagged with its embed model and retrieval refuses a mismatched index (rebuild with `build-index`). Embeddings have **no fallback** (a different embedder = an incompatible vector space); generation does. This makes the latency lever simple: the default is **Ollama Cloud** (hosted, fast; `OLLAMA_API_KEY`) — or point `OLLAMA_HOST` at a free local server, or switch generation to `gemini`/`groq` — by flipping one config value.

## The concept — hybrid retrieval (docs **+** live numbers)

Plain RAG answers from documents only. MarketPulse's twist: when a question names a coin or asks
about price/movement, the assistant **also** runs a **templated** query against Gold and injects the
real numbers into the prompt. So *"What moved Bitcoin this week and by how much?"* returns a
news-grounded narrative **and** the actual 7-day % change from `gold_market_movers` — not a guess.
Retrieval grounds the *story*; Gold grounds the *numbers*. Templates (not LLM-generated SQL) keep it
safe and cheap; the coin map whitelists every value that reaches the SQL.

## The config-swap (how local ↔ AWS works)

The **same `rag/` code** runs in both places, selected only by `APP_ENV` (+ `AWS_ENDPOINT_URL` for S3):

| | Local (free twin) | AWS |
| --- | --- | --- |
| **LLM (embed + generate)** | any provider in `llm.yaml` — default **Ollama Cloud** (`OLLAMA_API_KEY`), or local Ollama via `OLLAMA_HOST`, or **Gemini/OpenAI/Groq/Anthropic** | same registry; **Bedrock** (Titan + Converse) for the in-AWS path |
| **Gold query** | **DuckDB** over the dbt Gold DB (`target/marketpulse.duckdb`) | **Athena** over the Glue Gold tables |
| **Docs + FAISS index (S3)** | LocalStack S3 (`boto3` + `AWS_ENDPOINT_URL`) | real S3 (`gold/rag/<env>/`) |
| **Interface** | CLI + `uvicorn` + `streamlit` (all local) | the same code as 3 Lambdas (gated) |
| **Cost** | **$0** | Lambda + Athena (capped) + Bedrock tokens + KB S3 → < $1/session |

No emulator/`if endpoint` branches in `rag/` — portability is the adapters + `AWS_ENDPOINT_URL`.

## Runbook

**Local (the DoD path).** Prereqs: the providers configured in `rag/config/llm.yaml`, with their
keys in `.env`. By default **embeddings use Gemini** (`GEMINI_API_KEY`) and **generation uses Ollama
Cloud** (`OLLAMA_API_KEY`) with a Gemini fallback — so **both keys** must be set (Ollama Cloud has no
embeddings API). For a free local generator instead, set `OLLAMA_HOST=http://localhost:11434`, run
`ollama serve` + `ollama pull <models>`, and switch the embed provider to one that embeds locally.
Then: LocalStack up + stack applied; Gold populated (Stage 3 batch + `dbt build`, and/or Stage 4
trades); export `BRONZE_BUCKET`/`GOLD_BUCKET` (`terraform output`) + `AWS_ENDPOINT_URL` + `test`/`test` creds.

```bash
export APP_ENV=local                         # (defaults to local; export so child uv processes inherit it)
uv run python -m rag.app.cli ingest          # RSS  -> bronze/docs/ (idempotent)
uv run python -m rag.app.cli build-index     # embed -> gold/rag/local/{index.faiss,meta.json}
uv run python -m rag.app.cli ask "What moved Bitcoin this week and by how much?"
# Service + UI:
uv run uvicorn rag.app.main:app --reload     # API  :8000
uv run streamlit run rag/ui/streamlit_app.py # chat :8501  (set RAG_API_URL if API not on localhost:8000)
```

**AWS (deferred, gated).** `APP_ENV=aws` runs the identical code against Bedrock + Athena. The
Lambdas/IAM are written in `infra/rag.tf` behind `enable_rag` (default false, requires
`enable_catalog`). Before any real apply: (1) request **Bedrock model access** in the console; (2)
build a Lambda **layer/container** with `faiss`+`numpy` (binary deps — a plain zip won't import) and
stage `build/rag-lambda`; (3) the Gold-data S3 read in the assistant policy assumes the `gold/`
prefix — align it with your `DBT_GOLD_DATA`. I run `terraform plan`/`validate`; **you** apply.

**Config knobs.** *Service* (`rag/app/core/config.py`): `RAG_FEED_URLS` (http(s) only — SSRF guard),
`RAG_CHUNK_SIZE`/`RAG_CHUNK_OVERLAP`, `RAG_TOP_K`, `RAG_HTTP_TIMEOUT_S`, `RAG_INDEX_PREFIX`,
`RAG_CORS_ORIGINS` (plus `APP_ENV`/`AWS_ENDPOINT_URL`/`BRONZE_BUCKET`/`GOLD_BUCKET`). *LLM* (provider,
models, host) live in **`rag/config/llm.yaml`**, overridable by `RAG_GEN_PROVIDER`/`RAG_EMBED_PROVIDER`
(+ `…_FALLBACK`) and the key/host env vars it references (`OLLAMA_API_KEY`, `OLLAMA_HOST`,
`GEMINI_API_KEY`, …).

## Key decisions

- **Standalone `rag/` project (user-directed scope expansion beyond §313's CLI).** Layered
  (core/llm/domain/services/repositories/schemas/api/ui), production-grade (global error handling,
  structured logging, DI, input validation), and **extractable** — one shared seam with the core.
- **Templated Gold SQL, not LLM-generated SQL.** Safer, cheaper, deterministic; the coin whitelist
  is the only value source. Athena's 100 MB scan cap bounds cost.
- **FAISS-in-S3, env-namespaced** (`gold/rag/<env>/`). Near-zero cost vs OpenSearch; per-env because
  Ollama (768-dim) and Titan dims differ, so indexes must not collide.
- **LLM port moved into `rag/`.** The QueryEngine stays the shared core port; the LLM handler belongs
  to its only consumer (the assistant), which keeps the package self-contained.
- **Streamlit ↔ FastAPI over HTTP** (decoupled); **token streaming** (SSE) for chat UX; per-question
  retrieval (multi-turn memory is deferred, §331).

## Reviews (pre-PR)

- **cost-reviewer → GO.** $0 while `enable_rag=false`; < $1 for a dev session if enabled; no
  OpenSearch / NAT / always-on resources; reuses the gold bucket. Flagged (pre-existing/deferred):
  Bedrock model access must be granted; AWS Budgets not yet in Terraform; faiss layer needed.
- **infra-reviewer → GO** after fixes (all **applied**): scoped the assistant's Gold read to
  `gold/*` (was whole-bucket) **[C1]**; refused wildcard-CORS-with-credentials + narrowed
  methods/headers **[C2]**; index role writes only (dropped `GetObject`) **[W1]**; env-namespaced
  index prefix **[W4]**; `http(s)`-only feed URLs **[W2]**; `defusedxml` for RSS **[W3]**; Lambda
  handlers now catch unexpected errors → structured 500 **[S3]**. (W5 *not* applied — the ingest
  Lambda never lists, so adding `ListBucket` would over-grant; least-privilege kept.)
- **Code-correctness pass (3 adversarial reviewers) → fixes applied, +24 edge-case tests:**
  Gold outage now **degrades to a docs-only answer** (never 502); **SSE streaming** does its
  fallible prep *eagerly in the route* (errors → clean HTTP) and emits a typed `error` event on
  mid-stream LLM failure (UI handles it); `top_k` is now actually threaded to retrieval (was a
  no-op); config validates ranges (`temperature`/`chunk`/`top_k` → `ConfigError`); Ollama retries
  5xx/429 + wraps non-JSON/error-envelope → `LLMError`; Bedrock wraps bad-shape + mid-stream errors
  → `LLMError`; the indexer guards an empty/short embed result; retrieval fails loud on a
  FAISS/metadata count mismatch and corrupt metadata; malformed stored docs are skipped + logged.

## Definition of done

- ✅ Idempotent RSS ingestion + deterministic chunking; FAISS-in-S3 build + retrieval; hybrid
  assistant (retrieve + templated Gold) composed by the LLM via the adapter.
- ✅ FastAPI service (SSE) + Streamlit chat UI + CLI; three gated Lambdas + least-privilege IAM.
- ✅ **Verified:** `ruff check` + `ruff format --check` clean; **152 pytest passing** (97
  RAG-specific, all offline); `terraform validate` clean on `infra/`; cost + infra + code-correctness
  reviews GO (fixes applied); no LocalStack/`if endpoint` branches in `rag/`.
- ⏸️ AWS-live apply intentionally deferred (gated off) — see *Deferred*.

## Deferred / future

- **AWS-live deploy:** Bedrock model access + a `faiss`/`numpy` Lambda **layer or container image** +
  staging `build/rag-lambda`. The IaC is written, gated, and `validate`-clean.
- **AWS Budgets in Terraform** (pre-existing gap; urgent before any spend — credits expire ~Jun 18).
- **Index caching across requests (perf):** the API builds `AssistantService` per request, so the
  FAISS index reloads from S3 each `/ask` (fine locally; on AWS adds S3 GET latency/cost). A
  process-lifetime singleton + `/index`-triggered refresh is the optimisation — deferred (the
  `RetrievalService` already caches per-instance; correctness is unaffected).
- **Generic, config-only LLM provider adapter.** Most hosted LLMs expose an **OpenAI-compatible**
  API (`/chat/completions` + `/embeddings`); OpenAI + Groq already share one `_openai_compat.py` base.
  The next step is to make that base **fully config-driven** — add `type: openai_compat` + `base_url`
  to each `llm.yaml` provider — so a new compatible provider (Together, Fireworks, DeepSeek, Mistral,
  OpenRouter, xAI, …) becomes a **config row with zero new code**. The genuinely non-compatible ones
  stay small bespoke handlers — Bedrock (SigV4/boto3), and Gemini/Anthropic kept on their native
  endpoints for the safety/finish-reason handling already written. That **hybrid** (one generic
  adapter + a few natives) is the standard production shape — the same split a library like LiteLLM
  makes internally. **Kept as-is for now** (no over-engineering for 6 providers); the provider
  allowlist in `llm.yaml` stays either way — it drives fail-fast validation and fallback and is the
  security boundary against arbitrary base URLs.
- **RAG upgrades (you, later, §331):** re-ranking, query rewriting, an evaluation harness,
  multi-turn conversation memory.
