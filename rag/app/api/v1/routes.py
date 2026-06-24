"""API v1 routes: health/ready, ingest, index, ask (blocking + SSE streaming)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from rag.app.api.deps import assistant_dep, config_dep
from rag.app.core.config import RagConfig
from rag.app.core.errors import RAGError
from rag.app.core.logging import get_logger
from rag.app.repositories.index_store import IndexStore
from rag.app.schemas.chat import (
    AskRequest,
    AskResponse,
    CitationOut,
    GoldFactOut,
    GoldOut,
    HealthResponse,
    IndexResponse,
    IngestResponse,
    ReadyResponse,
)
from rag.app.services.assistant import AssistantAnswer, AssistantService
from rag.app.services.indexing import IndexingService
from rag.app.services.ingestion import IngestionService

logger = get_logger(__name__)

router = APIRouter()


def _to_ask_response(answer: AssistantAnswer) -> AskResponse:
    """Map the internal answer dataclass to the API response model."""
    return AskResponse(
        answer=answer.answer,
        used_gold=answer.used_gold,
        citations=[
            CitationOut(
                n=c.n, title=c.title, url=c.url, source=c.source, score=c.score, snippet=c.snippet
            )
            for c in answer.citations
        ],
        gold=GoldOut(
            intent=answer.gold.intent,
            used=answer.gold.used,
            summary=answer.gold.summary,
            facts=[
                GoldFactOut(label=f.label, value=f.value, numeric=f.numeric, unit=f.unit)
                for f in answer.gold.facts
            ],
        ),
    )


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health(config: RagConfig = Depends(config_dep)) -> HealthResponse:
    """Liveness check."""
    return HealthResponse(env=config.app_env)


@router.get("/ready", response_model=ReadyResponse, tags=["ops"])
def ready(config: RagConfig = Depends(config_dep)) -> ReadyResponse:
    """Readiness check: is the FAISS index built and reachable?"""
    try:
        store = IndexStore(config)
        return ReadyResponse(ready=store.exists(), index_location=store.location)
    except RAGError as exc:
        return ReadyResponse(ready=False, detail=str(exc))


@router.post("/ingest", response_model=IngestResponse, tags=["pipeline"])
def ingest(config: RagConfig = Depends(config_dep)) -> IngestResponse:
    """Fetch the configured RSS feeds into ``bronze/docs/`` (idempotent)."""
    result = IngestionService(config).ingest()
    return IngestResponse(feeds=result.feeds, documents=result.documents, new=result.new)


@router.post("/index", response_model=IndexResponse, tags=["pipeline"])
def build_index(config: RagConfig = Depends(config_dep)) -> IndexResponse:
    """Embed all chunks and (re)build the FAISS index in S3."""
    result = IndexingService(config).build()
    return IndexResponse(documents=result.documents, chunks=result.chunks, dim=result.dim)


@router.post("/ask", response_model=AskResponse, tags=["chat"])
def ask(req: AskRequest, assistant: AssistantService = Depends(assistant_dep)) -> AskResponse:
    """Answer a question using documents + live Gold metrics (blocking)."""
    answer = assistant.answer(req.question, top_k=req.top_k)
    return _to_ask_response(answer)


@router.post("/ask/stream", tags=["chat"])
def ask_stream(
    req: AskRequest, assistant: AssistantService = Depends(assistant_dep)
) -> StreamingResponse:
    """Stream the answer as Server-Sent Events: a ``sources`` event, ``delta``s, then ``done``.

    The fallible prep (retrieval + Gold) runs **eagerly here** so its errors map to clean HTTP
    error responses via the global handlers; only the LLM token stream lives in the generator.
    """
    prepared = assistant.prepare(req.question, top_k=req.top_k)

    def event_stream():
        for event in assistant.stream(prepared):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
