"""FastAPI application factory for the MarketPulse RAG assistant.

Wires CORS, a request-id middleware, the global exception handlers, and the v1 router. Import
``app`` for ASGI servers: ``uvicorn rag.app.main:app``.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from rag.app.api.errors import register_exception_handlers
from rag.app.api.v1.routes import router as v1_router
from rag.app.core.config import RagConfig, get_config
from rag.app.core.errors import ConfigError
from rag.app.core.logging import get_logger

logger = get_logger(__name__)


def create_app(config: RagConfig | None = None) -> FastAPI:
    """Build and configure the FastAPI app."""
    cfg = config or get_config()
    app = FastAPI(
        title="MarketPulse RAG Assistant",
        version="0.1.0",
        description="Hybrid RAG over crypto news + live Gold metrics.",
    )

    # Never combine a wildcard origin with credentials (a browser-security footgun).
    if "*" in cfg.cors_origins:
        raise ConfigError(
            "RAG_CORS_ORIGINS must list explicit origins (no '*'): credentials are allowed."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "x-request-id"],
    )

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    register_exception_handlers(app)
    app.include_router(v1_router, prefix="/api/v1")
    logger.info("RAG API initialised", extra={"env": cfg.app_env})
    return app


app = create_app()
