"""Factory: build the active :class:`LLMHandler` from the YAML provider registry.

Reads ``rag/config/llm.yaml`` (+ env overrides), validates the selection (provider exists, API key
present, embed provider can embed), constructs the embed/gen handlers and any fallbacks, and wraps
them in a :class:`CompositeLLM`. Called wherever a handler is needed — provider selection happens
here, at startup/construction, exactly as configured.
"""

from __future__ import annotations

from rag.app.core.llm_config import LLMConfig, load_llm_config
from rag.app.core.logging import get_logger
from rag.app.llm.base import LLMHandler
from rag.app.llm.composite import CompositeLLM
from rag.app.llm.registry import build_handler

logger = get_logger(__name__)


def get_llm_handler(llm_config: LLMConfig | None = None) -> LLMHandler:
    """Return the configured composite LLM handler (embed + gen providers, with fallbacks).

    Args:
        llm_config: Optional pre-loaded config (defaults to :func:`load_llm_config`).

    Returns:
        A ready :class:`LLMHandler`.

    Raises:
        ConfigError: if the config/selection is invalid (unknown provider, missing key, etc.).
        LLMError: if a selected provider's handler fails to initialise.
    """
    cfg = llm_config or load_llm_config()
    params = cfg.params

    embedder = build_handler(cfg.require_resolved(cfg.embed_provider, role="embed"), params)
    generator = build_handler(cfg.require_resolved(cfg.gen_provider, role="generate"), params)
    generate_fallback = (
        build_handler(cfg.require_resolved(cfg.gen_fallback, role="generate"), params)
        if cfg.gen_fallback
        else None
    )

    logger.info(
        "LLM providers selected",
        extra={
            "embed_provider": cfg.embed_provider,
            "gen_provider": cfg.gen_provider,
            "gen_fallback": cfg.gen_fallback,
        },
    )
    return CompositeLLM(embedder=embedder, generator=generator, generate_fallback=generate_fallback)
