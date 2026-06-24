"""Global exception handlers: map errors to a consistent JSON body (never leak traces)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rag.app.core.errors import RAGError
from rag.app.core.logging import get_logger

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers for RAGError, request validation, and any unhandled exception."""

    @app.exception_handler(RAGError)
    async def _handle_rag_error(request: Request, exc: RAGError) -> JSONResponse:
        logger.warning(
            "RAG error",
            extra={"code": exc.code, "status": exc.status_code, "path": request.url.path},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": type(exc).__name__, "code": exc.code, "detail": str(exc)},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg', '')}"
            for err in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content={"error": "ValidationError", "code": "validation_error", "detail": detail},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "code": "internal_error",
                "detail": "An unexpected error occurred.",
            },
        )
