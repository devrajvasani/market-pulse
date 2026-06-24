"""Request/response models for the RAG API (validated, documented in OpenAPI)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """A question for the assistant."""

    question: str = Field(min_length=1, max_length=2000, description="The user's question.")
    top_k: int | None = Field(
        default=None, ge=1, le=20, description="Override retrieved-chunk count."
    )


class CitationOut(BaseModel):
    """A cited news source backing the answer."""

    n: int
    title: str
    url: str
    source: str
    score: float
    snippet: str


class GoldFactOut(BaseModel):
    """One live Gold metric used in the answer."""

    label: str
    value: str
    numeric: float | None = None
    unit: str | None = None


class GoldOut(BaseModel):
    """The Gold lookup result attached to an answer."""

    intent: str
    used: bool
    summary: str
    facts: list[GoldFactOut] = Field(default_factory=list)


class AskResponse(BaseModel):
    """The assembled answer with its citations and live metrics."""

    answer: str
    used_gold: bool
    citations: list[CitationOut] = Field(default_factory=list)
    gold: GoldOut


class IngestResponse(BaseModel):
    """Result of an ingestion run."""

    feeds: int
    documents: int
    new: int


class IndexResponse(BaseModel):
    """Result of an index build."""

    documents: int
    chunks: int
    dim: int


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str = "ok"
    service: str = "marketpulse-rag"
    env: str


class ReadyResponse(BaseModel):
    """Readiness payload (is the FAISS index built?)."""

    ready: bool
    index_location: str | None = None
    detail: str | None = None


class ErrorResponse(BaseModel):
    """Structured error body returned by the global exception handlers."""

    error: str
    code: str
    detail: str | None = None
