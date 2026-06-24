# MarketPulse RAG assistant

A standalone, **repo-extractable** hybrid RAG service: answer plain-English market questions using
**both** ingested crypto-news documents (FAISS retrieval) **and** live **Gold** metrics (a templated
query). Cloud-agnostic by `APP_ENV` — Ollama + DuckDB locally, Bedrock + Athena on AWS.

## Layout
- `app/core/` — config, JSON logging, error hierarchy (self-contained).
- `app/llm/` — the `LLMHandler` port + Ollama/Bedrock handlers + factory.
- `app/domain/` — `Document`, `Chunk` models.
- `app/services/` — ingestion, chunking, indexing, retrieval, gold, assistant (framework-free).
- `app/repositories/` — S3 doc store, S3 FAISS index store, and the Gold query seam.
- `app/schemas/` + `app/api/` — pydantic DTOs + the FastAPI service (SSE streaming).
- `ui/streamlit_app.py` — the chat UI (talks to the API over HTTP).
- `lambda_handler.py` — AWS deploy shape (ingest / index / assistant).
- `tests/` — offline tests (mocked LLM, moto S3, DuckDB fixtures, FastAPI TestClient).

## Run (local, $0)
```bash
ollama pull nomic-embed-text llama3.2:3b      # with `ollama serve` running
export APP_ENV=local BRONZE_BUCKET=… GOLD_BUCKET=… AWS_ENDPOINT_URL=http://localhost:4566
uv run python -m rag.app.cli ingest
uv run python -m rag.app.cli build-index
uv run uvicorn rag.app.main:app --reload      # API  :8000
uv run streamlit run rag/ui/streamlit_app.py  # chat :8501
```

## The one shared seam
`app/repositories/gold_query.py` imports `config.settings.get_gold_query_engine()` (the core
project's QueryEngine — Athena/DuckDB). To extract this folder into its own repo, replace that single
import with the new home's Gold query client. Everything else is self-contained.

Full design + runbook: [`../docs/stages/stage-5-rag.md`](../docs/stages/stage-5-rag.md).
