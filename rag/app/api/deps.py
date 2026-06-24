"""FastAPI dependency providers (injection points; overridable in tests)."""

from __future__ import annotations

from rag.app.core.config import RagConfig, get_config
from rag.app.services.assistant import AssistantService


def config_dep() -> RagConfig:
    """Provide the active RAG configuration."""
    return get_config()


def assistant_dep() -> AssistantService:
    """Provide an :class:`AssistantService` wired to the active config."""
    return AssistantService(get_config())
