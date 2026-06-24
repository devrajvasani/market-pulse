"""RAG assistant error hierarchy (self-contained, for clean repo extraction).

A small, explicit set of exceptions so failures carry context and the API layer can map each
to a clear HTTP status + machine-readable ``code`` instead of leaking stack traces. Catch
:class:`RAGError` to handle any error this service raises, or a specific subclass per domain.
Each class advertises a default ``status_code`` (HTTP) and ``code`` (stable string) that the
FastAPI global exception handler turns into a structured JSON error body.
"""

from __future__ import annotations


class RAGError(Exception):
    """Base class for every error raised by the RAG assistant."""

    status_code: int = 500
    code: str = "rag_error"


class ConfigError(RAGError):
    """Raised when RAG configuration is missing, invalid, or inconsistent."""

    status_code = 500
    code = "config_error"


class LLMError(RAGError):
    """Raised when the embeddings/generation provider (Ollama or Bedrock) fails."""

    status_code = 502
    code = "llm_error"


class IngestionError(RAGError):
    """Raised when document ingestion (fetch, clean, or store) fails."""

    status_code = 502
    code = "ingestion_error"


class RetrievalError(RAGError):
    """Raised when retrieval (embedding the query or searching the index) fails."""

    status_code = 500
    code = "retrieval_error"


class IndexNotFoundError(RetrievalError):
    """Raised when the FAISS index has not been built yet (run ``build-index`` first)."""

    status_code = 409
    code = "index_not_found"


class GoldQueryError(RAGError):
    """Raised when the hybrid Gold query (Athena/DuckDB) fails."""

    status_code = 502
    code = "gold_query_error"
