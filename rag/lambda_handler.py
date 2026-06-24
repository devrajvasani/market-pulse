"""AWS Lambda entry points for the RAG assistant — the cloud deploy shape.

Three handlers wrap the same services used by the CLI/API:
  * ``assistant_handler`` — answer a question (direct invoke or API Gateway/Function URL proxy),
  * ``ingest_handler``   — fetch RSS feeds into ``bronze/docs/`` (scheduled in Stage 6),
  * ``index_handler``    — rebuild the FAISS index.

Deferred this stage: the assistant runs locally via the CLI/API. A real Bedrock deployment needs a
Lambda **layer or container image** carrying faiss + numpy (binary deps) — see
``docs/stages/stage-5-rag.md``. Terraform is written but gated behind ``var.enable_rag``.
"""

from __future__ import annotations

import json
from typing import Any

from rag.app.core.config import get_config
from rag.app.core.errors import RAGError
from rag.app.core.logging import get_logger
from rag.app.services.assistant import AssistantService, serialize_answer
from rag.app.services.indexing import IndexingService
from rag.app.services.ingestion import IngestionService

logger = get_logger(__name__)


def _question_from(event: Any) -> str:
    """Extract the question from a direct invoke or an API Gateway/Function URL proxy event."""
    if not isinstance(event, dict):
        return ""
    if isinstance(event.get("question"), str):
        return event["question"]
    body = event.get("body")
    if isinstance(body, dict):
        return str(body.get("question", ""))
    if isinstance(body, str):
        try:
            return str(json.loads(body).get("question", ""))
        except (ValueError, AttributeError):
            return ""
    return ""


def _response(status: int, payload: dict) -> dict:
    """Build an API-Gateway-compatible proxy response."""
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }


def _error(exc: RAGError) -> dict:
    """Map a RAGError to a structured error response."""
    return _response(
        exc.status_code, {"error": type(exc).__name__, "code": exc.code, "detail": str(exc)}
    )


def _safe(label: str, run) -> dict:
    """Run a handler body, mapping RAGError + any unexpected error to a structured response."""
    try:
        return run()
    except RAGError as exc:
        logger.warning("%s error", label, extra={"code": exc.code})
        return _error(exc)
    except Exception:
        logger.exception("Unhandled %s error", label)
        return _response(
            500,
            {
                "error": "InternalServerError",
                "code": "internal_error",
                "detail": "An unexpected error occurred.",
            },
        )


def assistant_handler(event: dict, context: object | None = None) -> dict:
    """Answer a question from the event; return an API-Gateway proxy response."""
    question = _question_from(event)
    if not question:
        return _response(
            400,
            {
                "error": "BadRequest",
                "code": "missing_question",
                "detail": "'question' is required.",
            },
        )

    def _run() -> dict:
        return _response(200, serialize_answer(AssistantService().answer(question)))

    return _safe("Assistant", _run)


def ingest_handler(event: dict, context: object | None = None) -> dict:
    """Run document ingestion (RSS → bronze/docs/); return counts."""

    def _run() -> dict:
        result = IngestionService(get_config()).ingest()
        return _response(
            200, {"feeds": result.feeds, "documents": result.documents, "new": result.new}
        )

    return _safe("Ingest", _run)


def index_handler(event: dict, context: object | None = None) -> dict:
    """Rebuild the FAISS index; return counts."""

    def _run() -> dict:
        result = IndexingService(get_config()).build()
        return _response(
            200, {"documents": result.documents, "chunks": result.chunks, "dim": result.dim}
        )

    return _safe("Index", _run)
